from __future__ import annotations

from io import BytesIO
import pytest

from app import database
from app.services import content_assets, content_workbench, voice_profiles, voice_synthesis
from app.video_engine.services import task, voice


@pytest.fixture
def clone_voice(tmp_path, monkeypatch):
    # 仅使用临时资产和数据库；替换媒体探测，不运行声音模型或付费接口。
    monkeypatch.setenv('AI_CUSTOMER_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('AI_CUSTOMER_DB', str(tmp_path / 'voice.sqlite3'))
    database.init_db()
    monkeypatch.setattr(content_assets, '_read_metadata', lambda *_: {'width': None, 'height': None, 'duration': None, 'thumbnail_path': ''})
    asset = content_assets.import_asset_file('reference.wav', BytesIO(b'reference-audio'), 'audio/wav')
    profile = voice_profiles.create_profile({'name': '已授权音色', 'reference_asset_id': asset['id'], 'prompt_text': '提交时参考文字', 'style_prompt': '提交时风格', 'consent_confirmed': True})
    return profile, asset


def test_video_generation_and_retry_keep_server_captured_voice_snapshot(clone_voice, monkeypatch):
    profile, asset = clone_voice
    expected = {'reference_asset_id': asset['id'], 'prompt_text': '提交时参考文字', 'style_prompt': '提交时风格'}
    job = content_workbench.create_video_job({
        'video_subject': '音色快照', 'voice_name': f"voxcpm2:{profile['id']}",
        'voice_profile_snapshot': {'reference_asset_id': 'forged', 'prompt_text': '伪造文字', 'style_prompt': '伪造风格', 'consent_confirmed': 'true'},
    }, [])
    assert job['params']['voice_profile_snapshot'] == expected
    voice_profiles.update_profile(profile['id'], {'prompt_text': '排队后参考文字', 'style_prompt': '排队后风格'})
    replacement = content_assets.import_asset_file('replacement.wav', BytesIO(b'replacement-audio'), 'audio/wav')
    # 注入档案引用变更，验证实际合成仍绑定原资产，而不是只保存了原 ID。
    with database.connect() as conn:
        conn.execute('UPDATE voice_profiles SET reference_asset_id=? WHERE id=?', (replacement['id'], profile['id']))
    synthesized = []

    def fake_synthesize(current, _text, reference, output, _rate, _volume):
        synthesized.append((current['prompt_text'], current['style_prompt'], reference))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'generated-audio')

    def fake_render(engine_id, *_args):
        path = database.get_video_generation_root() / 'tasks' / engine_id / 'final.mp4'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'generated-video')
        return [str(path)], [str(path)]

    # 保留创建→真实 task.start→generate_audio→tts→合成适配器调用链。
    monkeypatch.setattr(voice_synthesis, '_synthesize_voxcpm2', fake_synthesize)
    monkeypatch.setattr(voice, 'get_audio_duration', lambda *_: 2)
    monkeypatch.setattr(task, 'generate_script', lambda *_: '视频旁白')
    monkeypatch.setattr(task, 'generate_terms', lambda *_: ['素材'])
    monkeypatch.setattr(task, 'generate_subtitle', lambda *_: '')
    monkeypatch.setattr(task, 'get_video_materials', lambda *_: ['material.mp4'])
    monkeypatch.setattr(task, 'generate_final_videos', fake_render)
    assert content_workbench.run_video_job(job['id'])['status'] == 'succeeded'
    with database.connect() as conn:
        conn.execute("UPDATE runtime_jobs SET status='succeeded' WHERE id=?", (job['runtime_job_id'],))
    content_workbench.retry_video_job(job['id'])
    assert content_workbench.run_video_job(job['id'])['attempt'] == 2
    reference = content_assets.resolve_asset_path(asset['relative_path'])
    assert synthesized == [('提交时参考文字', '提交时风格', reference)] * 2
    # 独立配音明确读取当前档案，不属于旧视频任务的快照兜底。
    voice.tts('独立旁白', f"voxcpm2:{profile['id']}", 1.0, str(reference.parent / 'standalone.mp3'))
    assert synthesized[-1] == ('排队后参考文字', '排队后风格', content_assets.resolve_asset_path(replacement['relative_path']))


@pytest.mark.parametrize('invalid', ['missing_reference', 'revoked_consent', 'missing_snapshot'])
def test_invalid_clone_input_fails_before_billable_video_script(clone_voice, monkeypatch, invalid):
    profile, asset = clone_voice
    job = content_workbench.create_video_job({'video_subject': '先检查参考音频', 'voice_name': f"voxcpm2:{profile['id']}"}, [])
    if invalid == 'missing_reference':
        replacement = content_assets.import_asset_file('replacement.wav', BytesIO(b'replacement-audio'), 'audio/wav')
        with database.connect() as conn:
            conn.execute('UPDATE voice_profiles SET reference_asset_id=? WHERE id=?', (replacement['id'], profile['id']))
        # 当前档案参考文件仍可用；移走原文件，任务必须拒绝而不能改用新文件。
        content_assets.resolve_asset_path(asset['relative_path']).unlink()
    else:
        with database.connect() as conn:
            if invalid == 'revoked_consent':
                conn.execute('UPDATE voice_profiles SET consent_confirmed=0 WHERE id=?', (profile['id'],))
            else:
                conn.execute("UPDATE video_jobs SET params=json_remove(params,'$.voice_profile_snapshot') WHERE id=?", (job['id'],))
    billable = []
    monkeypatch.setattr(task.llm, 'generate_script', lambda **_kwargs: billable.append(True))
    with pytest.raises(ValueError):
        content_workbench.run_video_job(job['id'])
    assert billable == []
    assert content_workbench.get_video_job(job['id'])['status'] == 'failed'


def test_client_snapshot_cannot_override_current_voice_authorization(clone_voice):
    profile, _asset = clone_voice
    with database.connect() as conn:
        conn.execute('UPDATE voice_profiles SET consent_confirmed=0 WHERE id=?', (profile['id'],))
    with pytest.raises(ValueError, match='授权'):
        content_workbench.create_video_job({'video_subject': '拒绝伪造授权', 'voice_name': f"voxcpm2:{profile['id']}", 'voice_profile_snapshot': {'consent_confirmed': 'true'}}, [])
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM video_jobs').fetchone()[0] == 0
