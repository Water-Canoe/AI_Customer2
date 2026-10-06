from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from app import database


def register_outputs(conn: Any, video_job_id: str, attempt: int, paths: list[str], script: str) -> list[str]:
    """每次生成保存独立成品；重试不会替换已被发布任务引用的文件。"""
    root = database.get_video_generation_root().resolve()
    job = conn.execute('SELECT subject FROM video_jobs WHERE id = ?', (video_job_id,)).fetchone()
    ids = []
    for index, file_path in enumerate(paths):
        path = Path(file_path).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError('生成结果不在视频运行目录中或文件不存在')
        output_id = uuid.uuid4().hex
        conn.execute(
            'INSERT INTO video_outputs(id,video_job_id,attempt,output_index,name,relative_path,subject,script,file_size) VALUES(?,?,?,?,?,?,?,?,?)',
            (output_id, video_job_id, attempt, index, path.name, path.relative_to(root).as_posix(), job['subject'], script, path.stat().st_size),
        )
        ids.append(output_id)
    return ids


def outputs_for_jobs(job_ids: list[str], conn: Any) -> dict[str, list[dict[str, Any]]]:
    if not job_ids:
        return {}
    placeholders = ','.join('?' for _ in job_ids)
    rows = conn.execute(f'SELECT o.*,j.draft_key FROM video_outputs o JOIN video_jobs j ON j.id=o.video_job_id WHERE o.video_job_id IN ({placeholders}) ORDER BY o.attempt,o.output_index', job_ids).fetchall()
    result: dict[str, list[dict[str, Any]]] = {job_id: [] for job_id in job_ids}
    for output in _format_outputs(rows, conn):
        result[output['video_job_id']].append(output)
    return result


def get_output(output_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute('SELECT o.*,j.draft_key FROM video_outputs o JOIN video_jobs j ON j.id=o.video_job_id WHERE o.id=?', (output_id,)).fetchone()
        if row is None:
            raise ValueError('视频成品不存在')
        return _format_outputs([row], conn)[0]


def resolve_output_path(output_id: str) -> Path:
    output = get_output(output_id)
    root = database.get_video_generation_root().resolve()
    path = (root / output['relative_path']).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError('视频输出文件不存在或超出运行目录')
    return path


def list_outputs(page: int = 1, page_size: int = 20, search: str = '', video_job_id: str = '') -> dict[str, Any]:
    conditions, params = [], []
    if search:
        conditions.append('(instr(lower(o.subject),lower(?))>0 OR instr(lower(o.name),lower(?))>0)')
        params.extend([search, search])
    if video_job_id:
        conditions.append('o.video_job_id=?')
        params.append(video_job_id)
    where = ' AND '.join(conditions) or '1=1'
    with database.connect() as conn:
        total = conn.execute(f'SELECT COUNT(*) FROM video_outputs o WHERE {where}', params).fetchone()[0]
        rows = conn.execute(f'SELECT o.*,j.draft_key FROM video_outputs o JOIN video_jobs j ON j.id=o.video_job_id WHERE {where} ORDER BY o.created_at DESC,o.id DESC LIMIT ? OFFSET ?', [*params, page_size, (page-1)*page_size]).fetchall()
        items = _format_outputs(rows, conn)
    return {'items': items, 'total': total, 'page': page, 'page_size': page_size}


def _format_outputs(rows: list[Any], conn: Any) -> list[dict[str, Any]]:
    if not rows:
        return []
    ids = [row['id'] for row in rows]
    tasks = conn.execute(f"SELECT t.id,t.output_id,t.account_id,t.status,t.title,t.published_at,t.error,a.name AS account_name,a.platform FROM publish_tasks t JOIN publish_accounts a ON a.id=t.account_id WHERE t.output_id IN ({','.join('?' for _ in ids)}) ORDER BY t.created_at DESC,t.id DESC", ids).fetchall()
    by_output: dict[str, list[dict[str, Any]]] = {output_id: [] for output_id in ids}
    for task in tasks:
        by_output[task['output_id']].append(dict(task))
    return [dict(row) | {'output_id': row['id'], 'index': row['output_index'], 'size': row['file_size'], 'url': f"/api/content/outputs/{row['id']}/file", 'publish_tasks': by_output[row['id']]} for row in rows]
