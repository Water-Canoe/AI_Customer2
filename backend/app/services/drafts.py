from __future__ import annotations

import json
import re
from typing import Any

from app import database
from app.schemas import MessagePlanConfig, KeywordLeadPlanConfig, TaskCreate, TrafficAutomationPlanConfig, TrafficPlanCreate


# 草稿允许未完成的表单；只保存明确的业务字段，不保存设置、密钥或登录态。
CRAWL_FIELDS = set(TaskCreate.model_fields) | {'keyword_tags', 'creator_id_tags', 'specified_id_tags'}
TRAFFIC_FIELDS = set(TrafficPlanCreate.model_fields)
AUTOMATION_FIELDS = {'id', 'name', 'plan_type', 'weekdays', 'run_time', 'enabled'}
VIDEO_FIELDS = {'subject', 'asset_ids', 'audio_asset_id', 'bgm_asset_id', 'voice_provider', 'voice_mode'}
VIDEO_PARAMS = {
    'video_subject', 'video_script', 'video_terms', 'video_source', 'video_aspect', 'video_concat_mode',
    'video_transition_mode', 'video_clip_duration', 'match_materials_to_script', 'video_count',
    'video_language', 'voice_name', 'voice_volume', 'voice_rate', 'bgm_type', 'bgm_volume',
    'subtitle_enabled', 'subtitle_position', 'custom_position', 'font_name', 'text_fore_color',
    'text_background_color', 'rounded_subtitle_background', 'font_size', 'stroke_color',
    'stroke_width', 'n_threads', 'paragraph_number', 'video_script_prompt', 'custom_system_prompt',
}
PUBLISH_FIELDS = {'title', 'description', 'tags', 'account_ids', 'publish_strategy', 'scheduled_at'}
PUBLISH_PLAN_FIELDS = {'enabled', 'account_ids', 'output_scope', 'publish_strategy', 'scheduled_at'}
PUBLISH_OPTIONS = {'thumbnail_landscape_path', 'thumbnail_portrait_path', 'thumbnail_path', 'product_link', 'product_title', 'bgm'}
CONTEXT_QUERY_FIELDS = {'task', 'job', 'create', 'plan', 'run', 'draft', 'output', 'keyword', 'platform', 'status', 'page', 'search', 'edit', 'copy', 'type', 'tab', 'customer', 'page_size', 'query', 'result'}
KINDS = {'crawl', 'video', 'publish', 'traffic', 'automation'}


class DraftAlreadySubmitted(ValueError):
    pass


def record_submission(conn: Any, kind: str, key: str, context: dict[str, Any], *, entity_id: str = '') -> None:
    """与业务 INSERT 共用写事务，POST成功后清理失败也不会再次创建任务。"""
    if not key:
        return
    row = conn.execute('SELECT payload,submitted FROM drafts WHERE kind=? AND key=?', (kind, key)).fetchone()
    if row is None:
        raise ValueError('提交前请先保存表单草稿')
    if row['submitted']:
        raise DraftAlreadySubmitted('此草稿已提交，请打开已有记录或清理草稿，不要重复创建')
    payload = json.loads(row['payload'])
    payload['return_context'] = context
    if kind == 'automation':
        payload['id'] = entity_id
    conn.execute("UPDATE drafts SET payload=?,submitted=1,updated_at=datetime('now','localtime') WHERE kind=? AND key=?", (json.dumps(payload, ensure_ascii=False), kind, key))


def _pick(value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item for key, item in value.items() if key in fields
        and (item is None or isinstance(item, (str, int, float, bool))
             or isinstance(item, list) and all(element is None or isinstance(element, (str, int, float, bool)) for element in item))
    }


def sanitize_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError('未知草稿类型')
    if kind == 'crawl':
        result = _pick(payload, CRAWL_FIELDS)
    elif kind == 'traffic':
        result = _pick(payload, TRAFFIC_FIELDS)
    elif kind == 'automation':
        result = _pick(payload, AUTOMATION_FIELDS)
        fields = {'keyword_lead': KeywordLeadPlanConfig, 'message': MessagePlanConfig, 'traffic': TrafficAutomationPlanConfig}
        model = fields.get(str(result.get('plan_type') or 'keyword_lead'))
        result['config'] = _pick(payload.get('config'), set(model.model_fields) if model else set())
    elif kind == 'video':
        result = _pick(payload, VIDEO_FIELDS)
        result['params'] = _pick(payload.get('params'), VIDEO_PARAMS)
        result['publish_plan'] = _pick(payload.get('publish_plan'), PUBLISH_PLAN_FIELDS)
    else:
        result = _pick(payload, PUBLISH_FIELDS)
        result['source'] = _pick(payload.get('source'), {'type', 'output_ids', 'asset_ids'})
        overrides = payload.get('account_overrides')
        if isinstance(overrides, dict):
            result['account_overrides'] = {}
            for account_id, value in overrides.items():
                item = _pick(value, PUBLISH_FIELDS - {'account_ids'})
                if isinstance(value, dict):
                    item['options'] = _pick(value.get('options'), PUBLISH_OPTIONS)
                result['account_overrides'][str(account_id)] = item
    context = payload.get('return_context')
    if isinstance(context, dict):
        result['return_context'] = _pick(context, {'selection'})
        route = str(context.get('route') or '')
        if re.fullmatch(r'/[a-zA-Z0-9_/-]*', route):
            result['return_context']['route'] = route
        result['return_context']['query'] = _pick(context.get('query'), CONTEXT_QUERY_FIELDS)
    return result


def _identity(kind: str, key: str) -> None:
    if kind not in KINDS or not key.strip() or len(key) > 200:
        raise ValueError('草稿类型或标识不合法')


def get_draft(kind: str, key: str) -> dict[str, Any]:
    _identity(kind, key)
    with database.connect() as conn:
        row = conn.execute('SELECT * FROM drafts WHERE kind = ? AND key = ?', (kind, key)).fetchone()
    return _format(row) if row else {'kind': kind, 'key': key, 'payload': {}, 'updated_at': None, 'exists': False, 'submitted': False}


def save_draft(kind: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    _identity(kind, key)
    serialized = json.dumps(sanitize_payload(kind, payload), ensure_ascii=False)
    if len(serialized.encode('utf-8')) > 256_000:
        raise ValueError('草稿内容过大')
    with database.connect() as conn:
        conn.execute("INSERT INTO drafts(kind,key,payload) VALUES(?,?,?) ON CONFLICT(kind,key) DO UPDATE SET payload=excluded.payload,updated_at=datetime('now','localtime') WHERE drafts.submitted=0", (kind, key, serialized))
    return get_draft(kind, key)


def delete_draft(kind: str, key: str) -> dict[str, Any]:
    _identity(kind, key)
    with database.connect() as conn:
        conn.execute('DELETE FROM drafts WHERE kind = ? AND key = ?', (kind, key))
    return {'ok': True, 'kind': kind, 'key': key}


def list_drafts(kind: str, page: int = 1, page_size: int = 20, search: str = '') -> dict[str, Any]:
    _identity(kind, 'list')
    where, params = 'kind = ?', [kind]
    if search:
        where += " AND instr(lower(payload), lower(?)) > 0"
        params.append(search)
    with database.connect() as conn:
        total = conn.execute(f'SELECT COUNT(*) FROM drafts WHERE {where}', params).fetchone()[0]
        rows = conn.execute(f'SELECT * FROM drafts WHERE {where} ORDER BY updated_at DESC,key DESC LIMIT ? OFFSET ?', [*params, page_size, (page-1)*page_size]).fetchall()
    return {'items': [_format(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size}


def _format(row: Any) -> dict[str, Any]:
    return {'kind': row['kind'], 'key': row['key'], 'payload': json.loads(row['payload']), 'updated_at': row['updated_at'], 'exists': True, 'submitted': bool(row['submitted'])}
