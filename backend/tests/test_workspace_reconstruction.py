from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from app import database
from app.services import account_actions, account_center, content_publish, content_workbench, drafts, importer, job_queue, message_attempts, video_outputs


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    # 所有记录、生成文件和备份均放在临时目录，不触及用户数据或外部平台。
    monkeypatch.setenv('AI_CUSTOMER_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('AI_CUSTOMER_DB', str(tmp_path / 'workspace.sqlite3'))
    database.init_db()
    from app.main import app
    from app.services import license_service
    monkeypatch.setattr(license_service, 'ensure_authorized_for', lambda *_: {'authorized': True})
    monkeypatch.setattr(license_service, 'ensure_authorized', lambda: {'authorized': True})
    return TestClient(app)


def publish_account(name: str, platform: str = 'dy') -> dict:
    account = account_center.create_account(platform, name, 'brand', ['publish'], [])
    account_center.set_account_state(account['id'], 'ready', checked=True)
    account_center.set_feature_status(account['id'], 'publish', 'ready')
    return account_center.get_account(account['id'])


def seed_output(job_id='video-1', attempt=1, script='成品旁白') -> dict:
    path = database.get_video_generation_root() / 'tasks' / job_id / f'attempt-{attempt}' / 'final.mp4'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f'video-{attempt}'.encode())
    with database.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO video_jobs(id,subject,status,params) VALUES(?,'成品主题','succeeded','{}')", (job_id,))
        output_id = video_outputs.register_outputs(conn, job_id, attempt, [str(path)], script)[0]
    return video_outputs.get_output(output_id)


def seed_customer(native_id='customer-1') -> int:
    with database.connect() as conn:
        account_id = importer._upsert_account(conn, 'dy', native_id, nickname='真实客户')
        conn.execute("INSERT INTO lead_user_accounts(account_id,screening_status,follow_status) VALUES(?,'目标客户','未私信')", (account_id,))
        return conn.execute('SELECT id FROM lead_user_accounts WHERE account_id=?', (account_id,)).fetchone()[0]


@pytest.mark.parametrize(('kind', 'payload', 'expected'), [
    ('crawl', {'platform': 'dy', 'keyword_tags': ['电动车'], 'account_id': 'a'}, {'platform': 'dy', 'keyword_tags': ['电动车'], 'account_id': 'a'}),
    ('traffic', {'name': '未完成计划', 'account_id': '', 'action_like': True}, {'name': '未完成计划', 'account_id': '', 'action_like': True}),
    ('automation', {'plan_type': 'message', 'config': {'count': 10, 'keyword': '电动车', 'api_key': 'secret'}}, {'plan_type': 'message', 'config': {'count': 10}}),
    ('video', {'subject': '未完成视频', 'voice_mode': 'file', 'params': {'video_script': '文案', 'api_key': 'secret'}}, {'subject': '未完成视频', 'voice_mode': 'file', 'params': {'video_script': '文案'}, 'publish_plan': {}}),
    ('publish', {'source': {'type': 'video_output', 'output_ids': ['out-1'], 'auth': 'secret'}, 'account_overrides': {'a': {'title': '标题', 'options': {'product_link': 'https://example.test', 'cookie': 'secret'}, 'api_key': 'secret'}}}, {'source': {'type': 'video_output', 'output_ids': ['out-1']}, 'account_overrides': {'a': {'title': '标题', 'options': {'product_link': 'https://example.test'}}}}),
])
def test_partial_drafts_persist_only_explicit_business_fields(workspace, kind, payload, expected):
    payload |= {'api_key': 'secret', 'cookie': 'secret', 'return_context': {'route': '/acquisition', 'query': {'edit': 'plan-1', 'tab': 'drafts', 'token': 'secret'}, 'selection': ['out-1']}}
    missing = workspace.get(f'/api/drafts/{kind}/new').json()
    assert missing['exists'] is False
    saved = workspace.put(f'/api/drafts/{kind}/new', json={'payload': payload})
    assert saved.status_code == 200
    expected['return_context'] = {'route': '/acquisition', 'query': {'edit': 'plan-1', 'tab': 'drafts'}, 'selection': ['out-1']}
    assert saved.json()['payload'] == expected
    assert workspace.get(f'/api/drafts/{kind}/new').json() == saved.json()
    assert 'secret' not in saved.text


def test_draft_library_search_pagination_delete_and_generation_snapshot(workspace):
    for key, subject in [('one', '旧视频'), ('two', '目标草稿'), ('three', '目标草稿2')]:
        drafts.save_draft('video', key, {'subject': subject, 'params': {'video_subject': subject}})
    page = workspace.get('/api/drafts/video', params={'search': '目标', 'page': 2, 'page_size': 1}).json()
    assert page['total'] == 2 and len(page['items']) == 1 and page['page'] == 2
    created = workspace.post('/api/content/video-jobs', json={'draft_key': 'one', 'params': {'video_subject': '提交时的主题', 'video_script': '提交时的文案', 'video_source': 'pexels'}, 'asset_ids': []})
    assert created.status_code == 200, created.text
    job = created.json()
    drafts.save_draft('video', 'one', {'subject': '后续编辑的主题'})
    assert content_workbench.get_video_job(job['id'])['params']['video_subject'] == '提交时的主题'
    assert job['draft_key'] == 'one'
    assert workspace.delete('/api/drafts/video/one').status_code == 200
    assert not drafts.get_draft('video', 'one')['exists']
    assert content_workbench.get_video_job(job['id'])['params']['video_script'] == '提交时的文案'


def test_retry_keeps_original_output_and_publishing_reference(workspace, monkeypatch):
    from app.video_engine.services import task
    first = seed_output()
    account = publish_account('发布账号')
    original_task = content_publish.create_video_output_tasks(output_ids=[first['id']], account_ids=[account['id']])[0]
    with database.connect() as conn:
        conn.execute("UPDATE video_jobs SET attempt=1,params=? WHERE id='video-1'", (json.dumps({'video_subject': '成品主题'}),))
    monkeypatch.setattr(job_queue, 'is_entity_cancel_requested', lambda *_: False)

    def generate(engine_task_id, _params, stop_at):
        assert engine_task_id == 'video-1/attempt-2'
        path = database.get_video_generation_root() / 'tasks' / engine_task_id / 'final.mp4'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'new-video')
        return {'videos': [str(path)], 'script': '第二次文案'}

    monkeypatch.setattr(task, 'start', generate)
    regenerated = content_workbench.run_video_job('video-1')
    assert len(regenerated['outputs']) == 2
    assert regenerated['outputs'][0]['id'] == first['id']
    second = regenerated['outputs'][1]
    assert second['id'] != first['id'] and second['attempt'] == 2
    assert content_publish.task_media_paths(original_task)[0].read_bytes() == b'video-1'
    assert workspace.get(first['url']).content == b'video-1'
    assert workspace.get(second['url']).content == b'new-video'
    assert video_outputs.get_output(first['id'])['script'] == '成品旁白'
    assert video_outputs.get_output(second['id'])['script'] == '第二次文案'
    assert workspace.patch(f"/api/content/outputs/{first['id']}/status", json={'upload_status': 'uploaded'}).json()['upload_status'] == 'uploaded'


def test_publishing_expands_outputs_by_account_and_preserves_blank_caption(workspace):
    first, second = seed_output('first'), seed_output('second')
    a, b = publish_account('账号甲'), publish_account('账号乙')
    response = workspace.post('/api/content/publish-tasks', json={
        'source': {'type': 'video_output', 'output_ids': [first['id'], second['id']]},
        'account_ids': [a['id'], b['id']], 'title': '公共标题', 'description': '',
        'account_overrides': {a['id']: {'title': '账号甲标题'}, b['id']: {'title': '账号乙标题', 'description': '账号乙正文', 'tags': ['乙']}},
    })
    assert response.status_code == 200, response.text
    tasks = response.json()
    assert len(tasks) == 4 and len({item['batch_id'] for item in tasks}) == 1
    assert {(item['output_id'], item['account_id']) for item in tasks} == {(output['id'], account['id']) for output in [first, second] for account in [a, b]}
    for item in tasks:
        assert item['title'] == ('账号甲标题' if item['account_id'] == a['id'] else '账号乙标题')
        assert item['description'] == ('' if item['account_id'] == a['id'] else '账号乙正文')


@pytest.mark.parametrize('missing_input', ['deleted_material', 'missing_audio', 'missing_bgm', 'deleted_voice', 'missing_reference'])
def test_video_rechecks_inputs_before_retry_and_paid_generation(workspace, monkeypatch, missing_input):
    from app.services import content_assets, voice_profiles
    from app.video_engine.services import task

    monkeypatch.setattr(content_assets, '_read_metadata', lambda *_: {'width': None, 'height': None, 'duration': None, 'thumbnail_path': ''})
    asset = content_assets.import_asset_file('source.mp4' if missing_input == 'deleted_material' else 'source.wav', BytesIO(b'input'), 'video/mp4' if missing_input == 'deleted_material' else 'audio/wav')
    params = {'video_subject': '重试检查输入', 'video_script': '', 'video_source': 'pexels'}
    asset_ids, audio_id, bgm_id = [], '', ''
    if missing_input == 'deleted_material':
        params['video_source'] = 'local'
        asset_ids = [asset['id']]
    elif missing_input == 'missing_audio':
        audio_id = asset['id']
    elif missing_input == 'missing_bgm':
        bgm_id = asset['id']
    else:
        profile = voice_profiles.create_profile({'name': '授权音色', 'reference_asset_id': asset['id'], 'consent_confirmed': True})
        params['voice_name'] = f"voxcpm2:{profile['id']}"
    job = content_workbench.create_video_job(params, asset_ids, audio_id, bgm_id)
    with database.connect() as conn:
        conn.execute("UPDATE video_jobs SET status='failed' WHERE id=?", (job['id'],))
        conn.execute("UPDATE runtime_jobs SET status='failed' WHERE id=?", (job['runtime_job_id'],))
    if missing_input == 'deleted_material':
        content_assets.delete_asset(asset['id'])
    elif missing_input == 'deleted_voice':
        voice_profiles.delete_profile(profile['id'])
    else:
        # 模拟文件被用户在应用外移走；只删除这一项临时测试文件。
        content_assets.resolve_asset_path(asset['relative_path']).unlink()

    with pytest.raises(ValueError):
        content_workbench.retry_video_job(job['id'])
    with pytest.raises(ValueError):
        job_queue.retry_job(job['runtime_job_id'])
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM runtime_jobs').fetchone()[0] == 1
        assert conn.execute('SELECT status FROM video_jobs WHERE id=?', (job['id'],)).fetchone()[0] == 'failed'

    generated = []
    monkeypatch.setattr(task, 'start', lambda *_args, **_kwargs: generated.append(True))
    with pytest.raises(ValueError):
        content_workbench.run_video_job(job['id'])
    assert generated == []
    assert content_workbench.get_video_job(job['id'])['status'] == 'failed'


@pytest.mark.parametrize('invalid', [{'title': '长' * 21}, {'description': '长' * 1001}, {'tags': [str(i) for i in range(6)]}, {'publish_strategy': 'scheduled', 'scheduled_at': (datetime.now()+timedelta(hours=1)).isoformat()}])
def test_publish_rejects_invalid_account_copy_without_partial_batch(workspace, invalid):
    output = seed_output()
    a, b = publish_account('合法账号'), publish_account('超限账号')
    with pytest.raises(ValueError):
        content_publish.create_video_output_tasks(output_ids=[output['id']], account_ids=[a['id'], b['id']], title='合法标题', account_overrides={b['id']: invalid})
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM publish_tasks').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM runtime_jobs').fetchone()[0] == 0
    old_source = workspace.post('/api/content/publish-tasks', json={'source': {'type': 'video_output', 'video_job_id': 'video-1', 'output_name': 'final.mp4'}, 'title': '合法标题'})
    assert old_source.status_code == 422


def test_output_library_has_all_publication_records_and_bounded_queries(workspace, monkeypatch):
    output = seed_output()
    account = publish_account('历史发布账号')
    with database.connect() as conn:
        conn.executemany("INSERT INTO publish_tasks(id,batch_id,account_id,source_type,content_type,video_job_id,output_id,title,status) VALUES(?,'history',?,'video_output','video','video-1',?,'历史任务','succeeded')", [(f'publish-{i}', account['id'], output['id']) for i in range(120)])
    statements = []
    original = database.connect

    @contextmanager
    def traced_connect(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(database, 'connect', traced_connect)
    result = video_outputs.list_outputs(search='成品', page_size=1)
    assert result['total'] == 1 and len(result['items'][0]['publish_tasks']) == 120
    assert len([sql for sql in statements if sql.lstrip().upper().startswith('SELECT')]) == 3


def test_content_status_counts_unpublished_stable_outputs_across_all_records(workspace):
    from app.services import workbench_status

    first, second = seed_output('published'), seed_output('unpublished')
    account = publish_account('作品统计账号')
    tasks = content_publish.create_video_output_tasks(output_ids=[first['id'], second['id']], account_ids=[account['id']], title='发布标题')
    with database.connect() as conn:
        conn.execute("UPDATE publish_tasks SET status='succeeded' WHERE id=?", (tasks[0]['id'],))
        conn.execute("UPDATE publish_tasks SET status='failed' WHERE id=?", (tasks[1]['id'],))
        conn.execute("UPDATE video_jobs SET archived=1 WHERE id='unpublished'")
    metrics = workbench_status.get_status('content')['metrics']
    assert metrics['unpublished_outputs'] == 1
    assert 'active_jobs' in metrics


def test_manual_confirmation_is_explicit_atomic_and_idempotent(workspace, monkeypatch):
    lead_id = seed_customer()
    with database.connect() as conn:
        before = conn.execute('SELECT follow_status FROM lead_user_accounts WHERE id=?', (lead_id,)).fetchone()[0]
    assert before == '未私信'
    message_attempts.update_message_limits(1, 1)
    # 准备消息不请求确认接口；读取客户或额度也不能产生发送记录。
    assert message_attempts.get_message_limits()['used_today'] == 0
    payload = {'follow_status': '已私信', 'record_message_attempt': True, 'note': '人工确认已发送私信'}
    assert workspace.patch(f'/api/overview/customers/{lead_id}/follow-status', json=payload).status_code == 200
    assert workspace.patch(f'/api/overview/customers/{lead_id}/follow-status', json=payload).status_code == 200
    assert message_attempts.get_message_limits()['used_today'] == 1
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM lead_status_events').fetchone()[0] == 1
        attempt = conn.execute('SELECT source,status FROM message_send_attempts').fetchone()
        assert tuple(attempt) == ('manual', 'succeeded')
    another = seed_customer('customer-2')

    def fail_record(*args, **kwargs):
        raise RuntimeError('模拟发送事实写入失败')

    monkeypatch.setattr(message_attempts, 'record_manual_message_attempt', fail_record)
    with pytest.raises(RuntimeError):
        account_actions.update_customer_follow_status(another, '已私信', record_message_attempt=True)
    with database.connect() as conn:
        assert conn.execute('SELECT follow_status FROM lead_user_accounts WHERE id=?', (another,)).fetchone()[0] == '未私信'
        assert conn.execute('SELECT COUNT(*) FROM lead_status_events WHERE lead_account_id=?', (another,)).fetchone()[0] == 0


def test_manual_confirmation_reuses_failed_attempt_and_still_blocks_automation(workspace):
    lead_id = seed_customer()
    message_attempts.update_message_limits(2, 2)
    attempt_id = message_attempts.reserve_message_attempt(lead_id, 'automatic', 'batch')
    message_attempts.finish_message_attempt(attempt_id, 'failed', '未确认')
    account_actions.update_customer_follow_status(lead_id, '已私信', record_message_attempt=True)
    with database.connect() as conn:
        assert conn.execute('SELECT id,status FROM message_send_attempts').fetchone()[:] == (attempt_id, 'succeeded')
    with pytest.raises(message_attempts.MessageQuotaReached, match='今天已尝试'):
        message_attempts.reserve_message_attempt(lead_id, 'automatic', 'retry')


def test_platform_identity_sec_uid_promotes_native_id_without_nickname_merging(workspace):
    with database.connect() as conn:
        assert importer._upsert_account(conn, 'dy', '', nickname='相同昵称') is None
        assert importer._upsert_account(conn, 'dy', '', nickname='相同昵称') is None
        first = importer._upsert_account(conn, 'dy', '', nickname='相同昵称', sec_uid='MS4w-sec')
        enriched = importer._upsert_account(conn, 'dy', 'native-1', sec_uid='MS4w-sec')
        native_only = importer._upsert_account(conn, 'dy', 'native-1')
        other_platform = importer._upsert_account(conn, 'ks', 'native-1')
        assert first == enriched == native_only
        assert other_platform != first
        assert conn.execute('SELECT COUNT(*) FROM user_accounts').fetchone()[0] == 2


def test_nullable_source_identity_deduplicates_repeated_imports(workspace):
    lead_id = seed_customer()
    with database.connect() as conn:
        account_id = conn.execute('SELECT account_id FROM lead_user_accounts WHERE id=?', (lead_id,)).fetchone()[0]
        for _ in range(3):
            conn.execute("INSERT OR IGNORE INTO account_sources(account_id,keyword,source_kind) VALUES(?,'电动车','keyword_author')", (account_id,))
            conn.execute("INSERT OR IGNORE INTO lead_sources(lead_account_id,keyword,source_type) VALUES(?,'电动车','demand_content')", (lead_id,))
        assert conn.execute('SELECT COUNT(*) FROM account_sources').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM lead_sources').fetchone()[0] == 1


def test_runtime_page_links_business_and_retry_updates_domain_state(workspace, monkeypatch):
    seed_output()
    account = publish_account('发布名称')
    tasks = content_publish.create_video_output_tasks(output_ids=[video_outputs.list_outputs()['items'][0]['id']], account_ids=[account['id']], title='发布标题')
    task_id = tasks[0]['id']
    with database.connect() as conn:
        conn.execute("UPDATE publish_tasks SET status='review_required' WHERE id=?", (task_id,))
        for i in range(30):
            conn.execute("INSERT INTO video_jobs(id,subject,status) VALUES(?,?,'failed')", (f'job-{i}', f'业务视频{i}'))
            conn.execute("INSERT INTO runtime_jobs(id,kind,entity_id,payload,status) VALUES(?,'video_generation',?,?,'failed')", (f'runtime-{i}', f'job-{i}', json.dumps({'video_job_id': f'job-{i}'})))
        conn.execute("INSERT INTO runtime_jobs(id,kind,entity_id,payload,status) VALUES('publish-runtime','content_publish',?,?,'failed')", (task_id, json.dumps({'publish_task_id': task_id})))
    statements = []
    original = database.connect

    @contextmanager
    def traced_connect(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(database, 'connect', traced_connect)
    page = job_queue.list_jobs(kind='video_generation', page_size=20)
    assert page['total'] == 30 and len(page['items']) == 20
    assert all(item['display_name'].startswith('视频生成 · 业务视频') for item in page['items'])
    assert all(item['detail_url'].startswith('/content-records?job=') for item in page['items'])
    assert len([sql for sql in statements if sql.lstrip().upper().startswith('SELECT')]) == 3
    assert not job_queue.get_job('publish-runtime')['retry_allowed']
    with pytest.raises(ValueError, match='核实结果'):
        job_queue.retry_job('publish-runtime')
    with pytest.raises(ValueError, match='人工核实'):
        content_publish.retry_task(task_id)
    retried = job_queue.retry_job('runtime-0')
    with database.connect() as conn:
        row = conn.execute("SELECT status,runtime_job_id FROM video_jobs WHERE id='job-0'").fetchone()
        assert tuple(row) == ('queued', retried['id'])
    assert not job_queue.get_job('runtime-0')['retry_allowed']


@pytest.mark.parametrize('platform', ['dy', 'ks', 'xhs'])
def test_ai_copy_prompt_matches_real_publish_limits(platform):
    from app.video_engine.services import llm
    prompt = llm.build_social_metadata_prompt('主题', platform=platform)
    assert 'at most 20 characters' in prompt and 'at most 1000 characters' in prompt
    assert 'exactly 5 strings' in prompt


def test_backup_restore_and_clear_cover_drafts_outputs_and_references(workspace):
    from app.services import data_management, maintenance
    drafts.save_draft('video', 'draft-1', {'subject': '备份草稿'})
    output = seed_output()
    account = publish_account('保留账号')
    task = content_publish.create_video_output_tasks(output_ids=[output['id']], account_ids=[account['id']])[0]
    content_publish.update_task_state(task['id'], 'succeeded')
    with database.connect() as conn:
        conn.execute("UPDATE runtime_jobs SET status='succeeded' WHERE id=?", (task['runtime_job_id'],))
    backup = data_management.create_backup('new_entities')
    cleared = maintenance.clear_all_data(maintenance.CONFIRM_TEXT, create_backup=False, include_crawler=False)
    assert cleared['project']['tables']['drafts'] == 1
    assert cleared['project']['tables']['video_outputs'] == 1
    assert not drafts.get_draft('video', 'draft-1')['exists']
    data_management.restore_backup(backup['id'], data_management.RESTORE_CONFIRM_TEXT)
    assert drafts.get_draft('video', 'draft-1')['payload']['subject'] == '备份草稿'
    assert video_outputs.get_output(output['id'])['publish_tasks'][0]['id'] == task['id']
    assert video_outputs.resolve_output_path(output['id']).read_bytes() == b'video-1'
    with database.connect() as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_shared_model_credentials_are_user_protected_and_never_copied_into_snapshots(workspace):
    response = workspace.put('/api/settings', json={'values': {'ai_base_url': 'https://model.example.test/v1', 'ai_model': 'shared-model', 'ai_api_key': 'global-private-key'}})
    assert response.status_code == 200 and response.json()['ai_api_key_configured'] is True
    saved = workspace.put('/api/content/settings', json={'values': {'model_connection_mode': 'shared', 'app': {'pexels_api_keys': ['material-private-key'], 'mimo_api_key': 'voice-private-key'}}}).json()
    assert saved['model_connection'] == {'mode': 'shared', 'base_url': 'https://model.example.test/v1', 'model': 'shared-model', 'api_key_configured': True}
    assert saved['app']['pexels_api_keys'] == [] and saved['app']['pexels_api_keys_configured']
    runtime = content_workbench._runtime_settings()
    assert runtime['app']['llm_provider'] == 'openai'
    assert runtime['app']['openai_api_key'] == 'global-private-key'
    assert runtime['app']['openai_model_name'] == 'shared-model'
    assert runtime['app']['mimo_api_key'] == 'voice-private-key'
    job = content_workbench.create_video_job({'video_subject': '主题', 'video_script': '文案', 'api_key': 'global-private-key'}, [])
    assert 'private-key' not in json.dumps(job)
    assert 'private-key' not in workspace.get('/api/runtime/jobs').text
    assert 'private-key' not in workspace.get('/api/settings').text
    assert 'content_video_config' not in workspace.get('/api/settings').json()
    with database.connect() as conn:
        values = dict(conn.execute("SELECT key,value FROM settings WHERE key IN ('ai_api_key','content_video_config')"))
        assert all(value.startswith('dpapi:user:') for value in values.values())
        assert 'private-key' not in json.dumps(values)
        content = json.loads(database.get_setting(conn, 'content_video_config'))
        assert content['app']['openai_api_key'] == ''  # 同一连接的密钥仅保存一份。


def test_key_empty_values_preserve_and_explicit_clear_removes_credentials(workspace):
    workspace.put('/api/settings', json={'values': {'ai_api_key': 'private-key'}})
    workspace.put('/api/settings', json={'values': {'ai_api_key': '', 'ai_model': 'changed-model'}})
    with database.connect() as conn:
        assert database.get_setting(conn, 'ai_api_key') == 'private-key'
    cleared = workspace.put('/api/settings', json={'values': {}, 'clear_secrets': ['ai_api_key']})
    assert cleared.status_code == 200 and cleared.json()['ai_api_key_configured'] is False
    workspace.put('/api/content/settings', json={'values': {'model_connection_mode': 'custom', 'app': {'llm_provider': 'openai', 'openai_api_key': 'custom-key', 'pexels_api_keys': ['material-key']}}})
    workspace.put('/api/content/settings', json={'values': {'app': {'openai_api_key': '', 'pexels_api_keys': []}}})
    assert content_workbench.get_settings(mask_secrets=False)['app']['openai_api_key'] == 'custom-key'
    response = workspace.put('/api/content/settings', json={'values': {}, 'clear_secrets': ['app.openai_api_key', 'app.pexels_api_keys']})
    assert response.status_code == 200
    assert not response.json()['app']['openai_api_key_configured']
    assert not response.json()['app']['pexels_api_keys_configured']
    assert not response.json()['model_connection']['api_key_configured']


def test_credentials_use_user_scope_and_refuse_unprotected_values(workspace, monkeypatch):
    from types import SimpleNamespace
    from app import device_identity, protected_settings
    crypt32, kernel32 = device_identity._windows_libraries()
    flags = []

    def capture_protect(*args):
        flags.append(args[5])
        return crypt32.CryptProtectData(*args)

    monkeypatch.setattr(device_identity, '_windows_libraries', lambda: (SimpleNamespace(CryptProtectData=capture_protect, CryptUnprotectData=crypt32.CryptUnprotectData), kernel32))
    protected = protected_settings.protect('真实 DPAPI 回归')
    assert protected_settings.unprotect(protected) == '真实 DPAPI 回归'
    assert flags == [device_identity.CRYPTPROTECT_UI_FORBIDDEN]
    with pytest.raises(ValueError, match='保护格式'):
        protected_settings.unprotect('plaintext-key')


def test_profile_backup_restores_both_login_kinds_and_protected_credentials(workspace):
    from app.services import data_management
    account = publish_account('备份登录账号')
    profiles = [account_center.account_auth_path(account['id'], kind) / 'Cookies' for kind in ['user', 'creator']]
    for index, path in enumerate(profiles):
        path.write_bytes(f'cookie-{index}'.encode())
    with database.connect() as conn:
        database.set_setting(conn, 'ai_api_key', 'old-private-key')
    backup = data_management.create_backup('profile_boundary')
    assert backup['secret_protection'] == 'windows-dpapi-current-user'
    assert backup['data_directories']['platform_accounts']['file_count'] == 2
    for path in profiles:
        path.write_bytes(b'changed')
    with database.connect() as conn:
        database.set_setting(conn, 'ai_api_key', 'new-private-key')
    data_management.restore_backup(backup['id'], data_management.RESTORE_CONFIRM_TEXT)
    assert [path.read_bytes() for path in profiles] == [b'cookie-0', b'cookie-1']
    with database.connect() as conn:
        assert database.get_setting(conn, 'ai_api_key') == 'old-private-key'


def test_restore_rejects_unreadable_user_protected_config_before_overwriting(workspace):
    from app import data_lifecycle
    from app.services import data_management
    with database.connect() as conn:
        database.set_setting(conn, 'ai_api_key', 'preserve-current-key')
    backup = data_management.create_backup('unreadable_credentials')
    backup_dir = database.get_backup_root() / backup['id']
    backup_db = backup_dir / 'ai_customer.sqlite3'
    tampered = sqlite3.connect(backup_db)
    tampered.execute("UPDATE settings SET value='dpapi:user:bm90LWFuLWF1dGhlbnRpY2F0ZWQtYmxvYg==' WHERE key='ai_api_key'")
    tampered.commit()
    tampered.close()
    manifest = json.loads((backup_dir / 'manifest.json').read_text(encoding='utf-8'))
    manifest['database_sha256'] = data_lifecycle._sha256(backup_db)
    (backup_dir / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='Windows 用户无法解密'):
        data_management.restore_backup(backup['id'], data_management.RESTORE_CONFIRM_TEXT)
    with database.connect() as conn:
        assert database.get_setting(conn, 'ai_api_key') == 'preserve-current-key'


def test_maintenance_blocks_live_browser_profile_even_without_queue_jobs(workspace, monkeypatch):
    from app.services import data_management, profile_manager
    monkeypatch.setattr(profile_manager, 'status', lambda: {'interactive_active': True, 'runtime_active': False})
    with pytest.raises(ValueError, match='账号目录'):
        data_management.create_backup('live_profile')


def test_model_urls_and_ai_errors_cannot_expose_usable_api_keys(workspace, monkeypatch):
    from app.services import ai_service
    from app.video_engine.config import config
    from app.video_engine.services import llm
    response = workspace.put('/api/settings', json={'values': {'ai_base_url': 'https://user:password@example.test/v1'}})
    assert response.status_code == 400 and 'password' not in response.text
    response = workspace.put('/api/content/settings', json={'values': {'app': {'openai_base_url': 'https://example.test/v1?api_key=private-key'}}})
    assert response.status_code == 400 and 'private-key' not in response.text
    lead_id = seed_customer()
    with database.connect() as conn:
        database.set_setting(conn, 'ai_api_key', 'private-key')
    job = ai_service.create_ai_job('lead', lead_id, run_now=False)

    def failing_model(*args, **kwargs):
        raise RuntimeError('网关错误 Authorization: Bearer private-key')

    monkeypatch.setattr(ai_service, 'call_openai_compatible', failing_model)
    with pytest.raises(Exception) as caught:
        ai_service.run_ai_job(job['id'])
    assert 'private-key' not in str(caught.value)
    assert 'private-key' not in ai_service.get_ai_job(job['id'])['error']
    monkeypatch.setitem(config.app, 'openai_api_key', 'private-key')
    assert 'private-key' not in llm._sanitize_error_message('HTTP failure with private-key')


def test_plan_run_filters_and_ai_detail_use_server_facts(workspace):
    from app.services import ai_service
    lead_id = seed_customer()
    ai = ai_service.create_ai_job('lead', lead_id, run_now=False)
    detail = workspace.get(f"/api/ai/jobs/{ai['id']}")
    assert detail.status_code == 200 and detail.json()['status'] == 'pending'
    assert detail.json()['target_name'] == '真实客户'
    assert isinstance(detail.json()['input_payload'], dict)
    assert workspace.get('/api/ai/jobs/missing-job').status_code == 404
    with database.connect() as conn:
        for index in range(2):
            plan_id = f'plan-{index}'
            conn.execute("INSERT INTO traffic_plans(id,name) VALUES(?,?)", (plan_id, f'引流{index}'))
            conn.execute("INSERT INTO automation_plans(id,name,plan_type,run_time) VALUES(?,?,'message','12:00')", (plan_id, f'自动化{index}'))
            for run in range(3):
                conn.execute("INSERT INTO traffic_runs(id,plan_id,status) VALUES(?,?,'completed')", (f'traffic-{index}-{run}', plan_id))
                conn.execute("INSERT INTO automation_runs(id,plan_id,plan_name,plan_type,trigger_type,status) VALUES(?,?,?,'message','manual','completed')", (f'automation-{index}-{run}', plan_id, f'自动化{index}'))
    traffic = workspace.get('/api/traffic/runs', params={'plan_id': 'plan-1'}).json()
    assert len(traffic) == 3 and all(item['plan_id'] == 'plan-1' for item in traffic)
    automation = workspace.get('/api/automation/runs', params={'plan_id': 'plan-1', 'page_size': 2}).json()
    assert automation['total'] == 3 and len(automation['items']) == 2
    assert all(item['plan_id'] == 'plan-1' for item in automation['items'])


def test_missing_author_preserves_evidence_without_creating_sendable_identity(workspace):
    raw = sqlite3.connect(':memory:')
    raw.row_factory = sqlite3.Row
    raw.execute('CREATE TABLE douyin_aweme(aweme_id TEXT,user_id TEXT,sec_uid TEXT,nickname TEXT,source_keyword TEXT)')
    raw.execute("INSERT INTO douyin_aweme VALUES('content-1','','','相同昵称','电动车')")
    raw.execute('CREATE TABLE douyin_aweme_comment(comment_id TEXT,aweme_id TEXT,user_id TEXT,sec_uid TEXT,nickname TEXT,content TEXT)')
    raw.execute("INSERT INTO douyin_aweme_comment VALUES('comment-1','content-1','','','相同昵称','咨询价格')")
    with database.connect() as conn:
        conn.execute("INSERT INTO crawl_jobs(id,name,mode,platform,login_type,crawler_type,keywords,collect_comments,execute_crawler) VALUES('evidence-task','证据采集','demand_content','dy','qrcode','search','电动车',1,0)")
    imported = importer._import_with_connections(raw, 'evidence-task')
    assert imported['contents'] == imported['comments'] == 1
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM user_accounts').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM lead_user_accounts').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM raw_source_refs').fetchone()[0] == 2
        assert conn.execute('SELECT author_account_id FROM contents').fetchone()[0] is None
        assert conn.execute('SELECT author_account_id FROM comments').fetchone()[0] is None
    raw.execute("UPDATE douyin_aweme SET user_id='verified-native',sec_uid='verified-sec'")
    importer._import_with_connections(raw, 'evidence-task')
    importer._import_with_connections(raw, 'evidence-task')
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM user_accounts').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM lead_sources').fetchone()[0] == 1
    raw.close()


def draft_submission_case(kind: str) -> tuple[str, dict, str, str, int]:
    """构造真实创建请求；账号只写就绪事实，队列不启动任何外部执行。"""
    if kind == 'publish':
        accounts = [publish_account('发布甲'), publish_account('发布乙')]
        outputs = [seed_output('输出甲'), seed_output('输出乙')]
        return '/api/content/publish-tasks', {'draft_key': 'new', 'source': {'type': 'video_output', 'output_ids': [item['id'] for item in outputs]}, 'account_ids': [item['id'] for item in accounts], 'title': '提交标题'}, 'publish_tasks', 'task', 4
    if kind == 'video':
        return '/api/content/video-jobs', {'draft_key': 'new', 'params': {'video_subject': '提交主题', 'video_script': '提交文案'}, 'asset_ids': []}, 'video_jobs', 'job', 1
    if kind == 'automation':
        return '/api/automation/plans', {'draft_key': 'new', 'name': '提交计划', 'plan_type': 'message', 'run_time': '12:00', 'weekdays': [1], 'enabled': False, 'config': {'count': 1}}, 'automation_plans', 'edit', 1
    account = account_center.create_account('dy', '执行账号', 'test', ['acquisition', 'traffic'], ['acquisition', 'traffic'])
    account_center.set_account_state(account['id'], 'ready', checked=True)
    account_center.set_all_feature_status(account['id'], 'ready')
    if kind == 'traffic':
        return '/api/traffic/plans', {'draft_key': 'new', 'name': '提交引流计划', 'account_id': account['id'], 'action_like': True, 'enabled': False}, 'traffic_plans', 'edit', 1
    return '/api/tasks', {'draft_key': 'new', 'mode': 'demand_content', 'platform': 'dy', 'keywords': '电动车', 'account_id': account['id'], 'execute_crawler': False}, 'crawl_jobs', 'task', 1


@pytest.mark.parametrize('kind', ['crawl', 'traffic', 'automation', 'video', 'publish'])
def test_submitted_draft_ack_survives_failed_cleanup_and_stale_autosave(workspace, kind):
    url, body, table, query_key, count = draft_submission_case(kind)
    drafts.save_draft(kind, 'new', {'name': '准备表单', 'subject': '准备表单'})
    first = workspace.post(url, json=body)
    assert first.status_code == 200, first.text
    entities = first.json() if isinstance(first.json(), list) else [first.json()]
    ack = drafts.get_draft(kind, 'new')
    assert ack['submitted'] is True
    assert ack['payload']['return_context']['query'][query_key] == entities[0]['id']
    if kind == 'automation':
        assert ack['payload']['id'] == entities[0]['id']
    if kind == 'publish':
        assert ack['payload']['return_context']['selection'] == [entity['id'] for entity in entities]
    # 模拟 POST 成功但客户端清理/ACK请求都失败，旧异步自动保存随后抵达。
    assert drafts.save_draft(kind, 'new', {}) == ack
    second = workspace.post(url, json=body)
    assert second.status_code == 409, second.text
    with database.connect() as conn:
        assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == count


@pytest.mark.parametrize('kind', ['crawl', 'traffic', 'automation', 'video', 'publish'])
def test_business_creation_rolls_back_if_draft_ack_cannot_be_recorded(workspace, monkeypatch, kind):
    url, body, table, _query_key, _count = draft_submission_case(kind)
    drafts.save_draft(kind, 'new', {})

    def fail_ack(*args, **kwargs):
        raise ValueError('模拟提交结果写入失败')

    monkeypatch.setattr(drafts, 'record_submission', fail_ack)
    response = workspace.post(url, json=body)
    assert response.status_code == 400
    with database.connect() as conn:
        assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM runtime_jobs').fetchone()[0] == 0
    assert drafts.get_draft(kind, 'new')['submitted'] is False


def test_concurrent_video_submission_of_one_draft_creates_one_job(workspace):
    from concurrent.futures import ThreadPoolExecutor
    drafts.save_draft('video', 'new', {'subject': '同一草稿'})

    def submit():
        try:
            return content_workbench.create_video_job({'video_subject': '同一草稿', 'video_script': '文案'}, [], draft_key='new')['id']
        except drafts.DraftAlreadySubmitted:
            return 'duplicate'

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: submit(), range(2)))
    assert result.count('duplicate') == 1
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM video_jobs').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM runtime_jobs').fetchone()[0] == 1


@pytest.mark.parametrize('kind', ['crawl', 'video', 'publish'])
def test_creation_and_draft_ack_roll_back_if_queue_insert_fails(workspace, monkeypatch, kind):
    url, body, table, _query_key, _count = draft_submission_case(kind)
    drafts.save_draft(kind, 'new', {})
    if kind == 'video':
        account = publish_account('等待自动发布账号')
        body['publish'] = {'enabled': True, 'account_ids': [account['id']], 'output_scope': 'all'}
    original_enqueue = job_queue.enqueue
    calls = []

    def fail_after_real_queue_insert(*args, **kwargs):
        assert kwargs['conn'] is not None
        result = original_enqueue(*args, **kwargs)
        calls.append(result['id'])
        if kind != 'publish' or len(calls) == 2:
            raise ValueError('模拟队列写入后异常')
        return result

    monkeypatch.setattr(job_queue, 'enqueue', fail_after_real_queue_insert)
    assert workspace.post(url, json=body).status_code == 400
    with database.connect() as conn:
        assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM runtime_jobs').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM publish_tasks').fetchone()[0] == 0
    assert drafts.get_draft(kind, 'new')['submitted'] is False


def test_generic_retry_rolls_back_business_state_if_queue_insert_fails(workspace, monkeypatch):
    seed_output()
    runtime = job_queue.enqueue_video_job('video-1')
    with database.connect() as conn:
        conn.execute("UPDATE video_jobs SET status='failed',runtime_job_id=? WHERE id='video-1'", (runtime['id'],))
        conn.execute("UPDATE runtime_jobs SET status='failed' WHERE id=?", (runtime['id'],))
    original_enqueue = job_queue.enqueue

    def fail_after_real_queue_insert(*args, **kwargs):
        original_enqueue(*args, **kwargs)
        raise ValueError('模拟重试队列写入失败')

    monkeypatch.setattr(job_queue, 'enqueue', fail_after_real_queue_insert)
    with pytest.raises(ValueError, match='队列写入失败'):
        job_queue.retry_job(runtime['id'])
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM runtime_jobs').fetchone()[0] == 1
        assert conn.execute("SELECT status,runtime_job_id FROM video_jobs WHERE id='video-1'").fetchone()[:] == ('failed', runtime['id'])


def test_traffic_execution_and_runtime_name_use_submitted_plan_snapshot(workspace, monkeypatch):
    from app.schemas import TrafficPlanCreate
    from app.services import traffic_workbench

    account = account_center.create_account('dy', '引流执行账号', 'test', ['traffic'], ['traffic'])
    account_center.set_account_state(account['id'], 'ready', checked=True)
    account_center.set_feature_status(account['id'], 'traffic', 'ready')
    plan = traffic_workbench.create_plan(TrafficPlanCreate(name='本次计划', source_mode='search_keyword', source_value='原关键词', action_like=True, round_video_limit=3, account_id=account['id']))
    run = traffic_workbench.create_run(plan['id'])
    runtime = job_queue.enqueue_traffic_run(run['id'])
    traffic_workbench.update_plan(plan['id'], TrafficPlanCreate(name='后续计划', source_mode='search_keyword', source_value='新关键词', action_follow=True, round_video_limit=9, account_id=account['id']))
    details = workspace.get(f"/api/traffic/runs/{run['id']}").json()
    assert details['plan_name'] == '本次计划'
    assert details['plan_snapshot']['source_value'] == '原关键词'
    assert details['plan_snapshot']['actions'] == ['like']
    assert details['plan_snapshot']['round_video_limit'] == 3
    assert job_queue.get_job(runtime['id'])['display_name'] == '引流 · 本次计划'
    executed = []
    monkeypatch.setattr(traffic_workbench, '_run_with_playwright', lambda _run_id, params: executed.append(params) or {})
    traffic_workbench.run_traffic_run(run['id'])
    assert executed == [details['plan_snapshot']]
    assert traffic_workbench.get_plan(plan['id'])['round_video_limit'] == 9


@pytest.mark.parametrize('kind', ['traffic', 'ai'])
def test_traffic_start_and_ai_retry_roll_back_if_queue_insert_fails(workspace, monkeypatch, kind):
    from app.services import ai_service

    if kind == 'traffic':
        plan_url, body, _table, _query, _count = draft_submission_case('traffic')
        body.pop('draft_key')
        plan = workspace.post(plan_url, json=body).json()
        url = f"/api/traffic/plans/{plan['id']}/runs"
    else:
        job = ai_service.create_ai_job('lead', seed_customer(), run_now=False)
        with database.connect() as conn:
            conn.execute("UPDATE analysis_jobs SET status='failed',error='原错误' WHERE id=?", (job['id'],))
        url = f"/api/ai/jobs/{job['id']}/retry"
    original_enqueue = job_queue.enqueue

    def fail_after_real_queue_insert(*args, **kwargs):
        assert kwargs['conn'] is not None
        original_enqueue(*args, **kwargs)
        raise ValueError('模拟队列入库后失败')

    monkeypatch.setattr(job_queue, 'enqueue', fail_after_real_queue_insert)
    if kind == 'traffic':
        assert workspace.post(url).status_code == 400
    else:
        with pytest.raises(ValueError, match='队列入库后失败'):
            workspace.post(url)
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM runtime_jobs').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM traffic_runs').fetchone()[0] == 0
        if kind == 'ai':
            assert conn.execute('SELECT status,error FROM analysis_jobs WHERE id=?', (job['id'],)).fetchone()[:] == ('failed', '原错误')


@pytest.mark.parametrize(('submitted_fill_only', 'current_fill_only'), [(False, True), (True, False)])
def test_message_batch_rechecks_current_safety_switch_without_send_fact_or_quota(workspace, submitted_fill_only, current_fill_only):
    from app.services import message_workbench

    lead_id = seed_customer()
    with database.connect() as conn:
        conn.execute("INSERT INTO message_batches(id,fill_only) VALUES('safety-batch',?)", (int(submitted_fill_only),))
        item_id = conn.execute("INSERT INTO message_batch_items(batch_id,lead_account_id,profile_url,script) VALUES('safety-batch',?,'https://example.test/customer','提交时话术')", (lead_id,)).lastrowid
        item = dict(conn.execute('SELECT * FROM message_batch_items WHERE id=?', (item_id,)).fetchone())
        batch = dict(conn.execute("SELECT * FROM message_batches WHERE id='safety-batch'").fetchone())
        # 模拟任务排队后设置改变；保存的业务话术仍使用原快照。
        database.set_setting(conn, 'auto_dm_fill_only', 'true' if current_fill_only else 'false')
    asyncio.run(message_workbench._run_batch_item(item, batch))
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM message_send_attempts').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM lead_status_events').fetchone()[0] == 0
        assert conn.execute('SELECT follow_status FROM lead_user_accounts WHERE id=?', (lead_id,)).fetchone()[0] == '未私信'
        assert conn.execute('SELECT status FROM message_batch_items WHERE id=?', (item_id,)).fetchone()[0] == 'skipped'


@pytest.mark.parametrize('retry', [False, True])
def test_message_batch_creation_and_retry_roll_back_queue_failure_without_blocking_next_batch(workspace, monkeypatch, retry):
    from app.services import message_workbench

    account = account_center.create_account('dy', '私信执行账号', 'test', ['message'], ['message'])
    account_center.set_account_state(account['id'], 'ready', checked=True)
    account_center.set_feature_status(account['id'], 'message', 'ready')
    lead_id = seed_customer()
    with database.connect() as conn:
        conn.execute("UPDATE lead_user_accounts SET script='原话术' WHERE id=?", (lead_id,))
        conn.execute("INSERT INTO lead_sources(lead_account_id,keyword,source_type) VALUES(?,'电动车','demand_content')", (lead_id,))
    body = {'platform': 'dy', 'keyword': '电动车', 'count': 1, 'interval_min_seconds': 0, 'interval_max_seconds': 0, 'account_id': account['id']}
    url = '/api/message-workbench/auto-message-batches'
    if retry:
        old = message_workbench.create_auto_message_batch(**body, run_now=False)
        with database.connect() as conn:
            conn.execute("UPDATE message_batches SET status='failed' WHERE id=?", (old['id'],))
            conn.execute("UPDATE message_batch_items SET status='failed' WHERE batch_id=?", (old['id'],))
        url += f"/{old['id']}/retry"
    original_enqueue = job_queue.enqueue
    inserted = []

    def fail_after_real_queue_insert(*args, **kwargs):
        assert kwargs['conn'] is not None
        runtime = original_enqueue(*args, **kwargs)
        inserted.append(runtime['id'])
        raise ValueError('模拟私信队列写入后失败')

    monkeypatch.setattr(job_queue, 'enqueue', fail_after_real_queue_insert)
    failed = workspace.post(url, json=None if retry else body)
    assert failed.status_code == 400, failed.text
    assert len(inserted) == 1
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM runtime_jobs').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM message_batches').fetchone()[0] == int(retry)
        assert conn.execute('SELECT COUNT(*) FROM message_batch_items').fetchone()[0] == int(retry)
        assert conn.execute('SELECT COUNT(*) FROM message_send_attempts').fetchone()[0] == 0
    # 失败的新建没有留下阻塞后续操作的 pending 批次。
    monkeypatch.setattr(job_queue, 'enqueue', original_enqueue)
    created = workspace.post(url, json=None if retry else body)
    assert created.status_code == 200, created.text
    assert created.json()['runtime_job']['kind'] == 'message_batch'


def test_automatic_competitor_child_creation_rolls_back_queue_failure_and_can_prepare_again(workspace, monkeypatch):
    from app.services import job_handlers

    account = account_center.create_account('dy', '自动采集账号', 'test', ['acquisition'], ['acquisition'])
    account_center.set_account_state(account['id'], 'ready', checked=True)
    account_center.set_feature_status(account['id'], 'acquisition', 'ready')
    with database.connect() as conn:
        conn.execute("INSERT INTO crawl_jobs(id,name,mode,platform,login_type,crawler_type,keywords,status,account_id) VALUES('source-task','竞品来源','competitor_discovery','dy','qrcode','search','电动车','succeeded',?)", (account['id'],))
        candidate_id = importer._upsert_account(conn, 'dy', 'candidate-native', nickname='竞品候选', sec_uid='candidate-sec')
        conn.execute("INSERT INTO account_sources(account_id,keyword,task_id,source_kind) VALUES(?,'电动车','source-task','competitor_discovery')", (candidate_id,))
        database.set_setting(conn, 'auto_analyze_competitors', 'true')
    original_enqueue = job_queue.enqueue
    inserted = []

    def fail_after_real_queue_insert(*args, **kwargs):
        assert kwargs['conn'] is not None
        inserted.append(original_enqueue(*args, **kwargs)['id'])
        raise ValueError('模拟派生分析队列写入失败')

    monkeypatch.setattr(job_queue, 'enqueue', fail_after_real_queue_insert)
    job_handlers.run_post_crawl_automation('source-task')
    assert len(inserted) == 1
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM runtime_jobs').fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM crawl_jobs WHERE mode='account_analysis'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_logs WHERE level='error' AND message LIKE '%派生分析队列写入失败%'").fetchone()[0] == 1
    monkeypatch.setattr(job_queue, 'enqueue', original_enqueue)
    job_handlers.run_post_crawl_automation('source-task')
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM runtime_jobs').fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM crawl_jobs WHERE mode='account_analysis'").fetchone()[0] == 1
