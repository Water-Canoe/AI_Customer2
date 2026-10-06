from __future__ import annotations

from typing import Any
from urllib.parse import urlencode


LABELS = {
    'crawl_task': '采集', 'crawl_batch': '批量采集', 'account_analysis': '账号分析',
    'keyword_account_analysis': '账号分析', 'ai_job': 'AI分析', 'ai_batch': '批量AI分析',
    'account_customer_intent': '客户意向分析', 'traffic_run': '引流',
    'message_batch': '自动私信批次', 'message_single': '客户私信',
    'automation_run': '自动化', 'video_generation': '视频生成', 'content_publish': '内容发布',
    'account_login': '账号登录', 'account_check': '登录检查', 'voice_runtime_install': '配音环境安装',
}


def enrich_jobs(jobs: list[dict[str, Any]], conn: Any) -> list[dict[str, Any]]:
    """按本页涉及的业务表批量取名称和状态，避免运行列表逐行查询。"""
    groups: dict[str, set[str]] = {}
    references: list[tuple[str, list[str]]] = []
    for job in jobs:
        kind, payload = job['kind'], job['payload']
        if kind in {'crawl_task', 'account_analysis', 'keyword_account_analysis'}:
            group, ids = 'crawl', [payload.get('task_id', '')]
        elif kind == 'crawl_batch':
            group, ids = 'crawl', payload.get('task_ids', [])
        elif kind in {'ai_job', 'ai_batch', 'account_customer_intent'}:
            group, ids = 'ai', [payload.get('job_id', '')] if kind == 'ai_job' else payload.get('job_ids', [])
        elif kind in {'account_login', 'account_check'}:
            group, ids = 'account', [payload.get('account_id', '')]
        else:
            fields = {'traffic_run': ('traffic', 'run_id'), 'message_batch': ('batch', 'batch_id'),
                      'message_single': ('lead', 'lead_id'), 'automation_run': ('automation', 'run_id'),
                      'video_generation': ('video', 'video_job_id'), 'content_publish': ('publish', 'publish_task_id')}
            group, field = fields.get(kind, ('', ''))
            ids = [payload.get(field, '')] if group else []
        clean_ids = [str(value) for value in ids if str(value)]
        references.append((group, clean_ids))
        groups.setdefault(group, set()).update(clean_ids)

    queries = {
        'crawl': 'SELECT id,name,status FROM crawl_jobs WHERE id',
        'ai': "SELECT j.id,j.status,COALESCE(CASE j.target_type WHEN 'competitor' THEN a.nickname WHEN 'lead' THEN la.nickname WHEN 'content' THEN c.title END,'') AS name FROM analysis_jobs j LEFT JOIN user_accounts a ON j.target_type='competitor' AND a.id=j.target_id LEFT JOIN lead_user_accounts l ON j.target_type='lead' AND l.id=j.target_id LEFT JOIN user_accounts la ON la.id=l.account_id LEFT JOIN contents c ON j.target_type='content' AND c.id=j.target_id WHERE j.id",
        'account': 'SELECT id,name,status FROM publish_accounts WHERE deleted_at IS NULL AND id',
        'traffic': "SELECT id,json_extract(plan_snapshot,'$.name') AS name,status FROM traffic_runs WHERE id",
        'batch': "SELECT id,keyword AS name,status FROM message_batches WHERE id",
        'lead': 'SELECT l.id,a.nickname AS name,l.follow_status AS status FROM lead_user_accounts l JOIN user_accounts a ON a.id=l.account_id WHERE l.id',
        'automation': 'SELECT id,plan_name AS name,status,plan_id FROM automation_runs WHERE id',
        'video': 'SELECT id,subject AS name,status FROM video_jobs WHERE id',
        'publish': "SELECT t.id,t.title || ' · ' || a.name AS name,t.status FROM publish_tasks t JOIN publish_accounts a ON a.id=t.account_id WHERE t.id",
    }
    objects: dict[str, dict[str, dict[str, Any]]] = {}
    for group, ids in groups.items():
        if not ids or group not in queries:
            continue
        rows = conn.execute(f"{queries[group]} IN ({','.join('?' for _ in ids)})", sorted(ids)).fetchall()
        objects[group] = {str(row['id']): dict(row) for row in rows}

    for job, (group, ids) in zip(jobs, references):
        matches = [objects.get(group, {}).get(value) for value in ids]
        matches = [item for item in matches if item]
        target = matches[0] if matches else None
        label = LABELS.get(job['kind'], job['kind'])
        name = str(target['name'] or '') if target else ''
        job['display_name'] = f'{label} · {name}' if name else label
        if len(ids) > 1:
            job['display_name'] += f'（{len(ids)}项）'
        job['detail_url'] = _detail_url(group, target) if target else ('/content-settings' if job['kind'] == 'voice_runtime_install' else '')
        # 私信和自动化从业务页重试；发布成功或结果不明时禁止重复提交。
        job['retry_allowed'] = (
            job['status'] in {'succeeded', 'failed', 'cancelled', 'interrupted'}
            and job['kind'] not in {'message_single', 'message_batch', 'automation_run'}
            and (bool(matches) and len(matches) == len(ids) or job['kind'] == 'voice_runtime_install')
            and all(item['status'] not in {'pending', 'queued', 'running', 'checking', 'waiting_media'} for item in matches)
            and (group != 'publish' or target['status'] in {'failed', 'cancelled'})
        )
    return jobs


def _detail_url(group: str, target: dict[str, Any]) -> str:
    routes = {
        'crawl': ('/acquisition', 'task'), 'ai': ('/ai', 'job'), 'account': ('/accounts', 'account'),
        'traffic': ('/traffic-monitor', 'run'), 'batch': ('/message-batches', 'batch'),
        'lead': ('/message-workbench', 'customer'), 'automation': ('/automation-plans', 'run'),
        'video': ('/content-records', 'job'), 'publish': ('/content-publish', 'task'),
    }
    route, key = routes[group]
    query = {key: target['id']}
    if group == 'lead':
        query['status'] = '全部'
    if group == 'automation' and target.get('plan_id'):
        query['plan'] = target['plan_id']
    return f'{route}?{urlencode(query)}'
