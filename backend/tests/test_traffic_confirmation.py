from __future__ import annotations

import json
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import traffic_workbench as traffic


VIDEO = {"video_id": "7300000000000000001", "author_id": "123456789"}


def response(path: str, body: str = "", *, method: str = "POST") -> SimpleNamespace:
    return SimpleNamespace(
        url="https://www.douyin.com" + path,
        status=200,
        request=SimpleNamespace(post_data=body, method=method),
        json=lambda: {"status_code": 0},
    )


@pytest.mark.parametrize("action,path,id_key", [
    ("like", "/aweme/v1/web/commit/item/digg/", "aweme_id"),
    ("collect", "/aweme/v1/web/aweme/collect/", "aweme_id"),
    ("follow", "/aweme/v1/web/commit/follow/user/", "user_id"),
])
def test_json_write_responses_are_matched_to_the_actual_target(action, path, id_key):
    target = VIDEO["author_id" if action == "follow" else "video_id"]
    item = response(path, json.dumps({id_key: target, "type": 1}))
    assert traffic._is_action_response(item, action, VIDEO)


def test_preflight_is_not_mistaken_for_the_write_response():
    item = response(
        "/aweme/v1/web/commit/item/digg/?aweme_id=7300000000000000001&type=1",
        method="OPTIONS",
    )
    assert not traffic._is_action_response(item, "like", VIDEO)


def test_disabled_cancel_flag_does_not_reject_a_positive_action():
    item = response(
        "/aweme/v1/web/aweme/collect/",
        "aweme_id=7300000000000000001&type=1&is_cancel=0",
    )
    assert traffic._is_action_response(item, "collect", VIDEO)


@pytest.mark.parametrize("body", [
    "user_id=987654321&type=1",
    "user_id=123456789&type=0",
    "user_id=123456789&type=1&is_cancel=1",
])
def test_other_authors_and_cancel_requests_are_not_confirmations(body):
    item = response("/aweme/v1/web/commit/follow/user/", body)
    assert not traffic._is_action_response(item, "follow", VIDEO)


def test_target_matching_uses_the_id_field_instead_of_a_substring():
    item = response(
        "/aweme/v1/web/commit/item/digg/",
        "aweme_id=97300000000000000001&type=1",
    )
    assert not traffic._is_action_response(item, "like", VIDEO)


@pytest.mark.parametrize("payload", [
    {"status_code": False},
    {"status_code": 8, "result": 1},
])
def test_explicit_platform_rejection_cannot_be_overridden_by_another_field(payload):
    assert not traffic._payload_status_ok(payload)


def test_action_diagnostics_preserve_the_actual_failure_and_never_repeat_submit():
    item = response("/aweme/v1/web/comment/publish/?msToken=must-not-be-logged")
    item.json = lambda: {"status_code": 8, "status_msg": "评论发送失败"}
    page = SimpleNamespace(expect_response=lambda *args, **kwargs: nullcontext(SimpleNamespace(value=item)))
    diagnostic = {}
    submitted = []
    result = traffic._comment_publish_response(page, lambda: submitted.append(True) or True, 8000, diagnostic)
    assert result is None
    assert submitted == [True]
    assert diagnostic["failure"] == "platform_rejected"
    assert diagnostic["status_code"] == 8
    assert diagnostic["status_msg"] == "评论发送失败"
    assert "must-not-be-logged" not in json.dumps(diagnostic)


def test_response_timeout_keeps_one_submission_and_a_specific_diagnosis():
    class Info:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            raise TimeoutError("no response")

    page = SimpleNamespace(expect_response=lambda *args, **kwargs: Info())
    diagnostic = {}
    submitted = []
    assert traffic._comment_publish_response(page, lambda: submitted.append(True) or True, 8000, diagnostic) is None
    assert submitted == [True]
    assert diagnostic["failure"] == "response_timeout"


@pytest.mark.parametrize("status,payload,failure", [
    (403, {}, "http_error"),
    (200, {}, "invalid_response"),
])
def test_http_and_unreadable_responses_have_distinct_diagnostics(status, payload, failure):
    item = response("/aweme/v1/web/comment/publish/")
    item.status, item.json = status, lambda: payload
    page = SimpleNamespace(expect_response=lambda *args, **kwargs: nullcontext(SimpleNamespace(value=item)))
    diagnostic = {}
    assert traffic._comment_publish_response(page, lambda: True, 8000, diagnostic) is None
    assert diagnostic["failure"] == failure
    assert diagnostic["http_status"] == status


def test_missing_like_control_does_not_send_an_unscoped_shortcut(monkeypatch):
    pressed = []
    page = SimpleNamespace(
        expect_response=lambda *args, **kwargs: nullcontext(),
        keyboard=SimpleNamespace(press=pressed.append),
    )
    monkeypatch.setattr(traffic, "_click_current_control", lambda *args, **kwargs: False)
    diagnostic = {}
    assert traffic._click_and_confirm_action_response(page, VIDEO, "like", [], diagnostic=diagnostic) is None
    assert diagnostic["failure"] == "control_unavailable"
    assert pressed == []


def test_kuaishou_write_confirmation_also_ignores_preflight():
    def expect(predicate, **_):
        preflight = response("/rest/v/photo/like", method="OPTIONS")
        write = response("/rest/v/photo/like", method="POST")
        write.json = lambda: {"result": 1}
        assert not predicate(preflight)
        assert predicate(write)
        return nullcontext(SimpleNamespace(value=write))

    payload = traffic._kuaishou_response_after_click(SimpleNamespace(expect_response=expect), "/rest/v/photo/like", lambda: True)
    assert traffic._kuaishou_action_confirmed(payload)


@pytest.mark.parametrize("kind", ["covered_control", "already_followed", "covered_comment_send", "read_current_video", "video_flags", "control_snapshot"])
def test_only_unobstructed_controls_can_be_selected(kind):
    class Page:
        def evaluate_handle(self, script, args):
            self.script, self.args = script, args
            return SimpleNamespace(as_element=lambda: None, dispose=lambda: None)

        def evaluate(self, script, args=None):
            self.script, self.args = script, args
            return {"video_id": "", "video_url": ""} if kind == "read_current_video" else None

    page = Page()
    if kind == "video_flags":
        traffic._current_video_flags(page, VIDEO["video_id"])
    elif kind == "control_snapshot":
        traffic._current_control_snapshot(page, VIDEO["video_id"], "like")
    elif kind == "read_current_video":
        traffic._read_active_video(page)
    elif kind == "covered_comment_send":
        traffic._comment_send_button_point(page)
    else:
        traffic._click_current_control(page, VIDEO["video_id"], "follow" if kind == "already_followed" else "like", require_confirm=False)
    # 用项目已有 jsdom 执行真实定位脚本，避免 FakePage 直接返回成功而漏测脚本。
    fixture = r"""
const { JSDOM } = require(require.resolve('jsdom', { paths: [process.argv[1]] }));
let input = '';
process.stdin.on('data', part => input += part);
process.stdin.on('end', () => {
  const { script, args, kind } = JSON.parse(input);
  const background = '<div data-e2e="feed-active-video" data-e2e-vid="other-video">广告 立即购买<button data-e2e="video-player-digg" id="behind" class="background-like">赞</button><button data-e2e="feed-follow" id="followed">已关注</button><div class="comment-input-inner-container"><button id="behind-send">发送</button></div></div>';
  const foreground = '<div class="video_7300000000000000001" data-e2e-vid="7300000000000000001"><button data-e2e="video-player-digg" id="front" class="front-like">赞</button><div class="comment-input-inner-container"><button id="front-send">发送</button></div></div>';
  const dom = new JSDOM(background + foreground, { runScripts: 'outside-only', url: 'https://www.douyin.com/discover?modal_id=7300000000000000001' });
  const win = dom.window;
  Object.defineProperty(win.HTMLElement.prototype, 'innerText', { get() { return this.textContent; } });
  win.HTMLElement.prototype.getBoundingClientRect = function () {
    const x = this.id.startsWith('front') ? 210 : 10;
    return { x, y: 10, top: 10, left: x, right: x + 100, bottom: 50, width: 100, height: 40 };
  };
  win.HTMLElement.prototype.scrollIntoView = () => {};
  win.document.elementFromPoint = () => win.document.querySelector(kind === 'covered_comment_send' ? '#front-send' : kind === 'already_followed' ? '#followed' : '#front');
  const value = win.eval('(' + script + ')')(args);
  const outcome = value && (kind === 'video_flags' ? value.is_ad : kind === 'control_snapshot' ? value.cls : value.id || value.video_id || value.x);
  console.log(JSON.stringify(outcome));
  win.close();
});
"""
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    result = subprocess.run(["node", "-e", fixture, str(frontend)],
                            input=json.dumps({"script": page.script, "args": page.args, "kind": kind}),
                            capture_output=True, text=True, encoding="utf-8", check=True, timeout=60)
    assert json.loads(result.stdout) == {"covered_control": "front", "already_followed": None, "covered_comment_send": 260, "read_current_video": VIDEO["video_id"], "video_flags": False, "control_snapshot": "front-like"}[kind]
