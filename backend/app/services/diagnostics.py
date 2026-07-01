from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app import database
from app.services import crawler_adapter


Rule = tuple[str, tuple[str, ...], str, list[str], bool]


DIAGNOSTIC_RULES: list[Rule] = [
    (
        "数据库缺表",
        ("no such table", "sqlite3.operationalerror", "douyin_aweme", "xhs_note", "kuaishou_video"),
        "MediaCrawler 底层数据库缺少任务需要读取的表，通常是路径指向了空库或未初始化的库。",
        ["在设置页确认 MediaCrawler 数据库路径指向实际采集库", "重新运行一次对应平台任务，让 MediaCrawler 初始化表结构", "如果刚换过 MediaCrawler 路径，确认新路径下 database/sqlite_tables.db 存在"],
        True,
    ),
    (
        "浏览器CDP",
        ("cdp port", "remote debugging", "chrome://inspect", "browser connection disconnected", "still waiting for browser"),
        "浏览器远程调试端口不可用或连接中断，MediaCrawler 没有拿到稳定的登录浏览器环境。",
        ["先关闭旧的浏览器调试进程，再重新启动后端和任务", "确认 Chrome/浏览器已开启 9222 远程调试端口", "如果使用扫码登录，保持登录窗口可用并避免手动关闭浏览器"],
        True,
    ),
    (
        "路径配置",
        ("media crawler path", "media_crawler_path", "no such file", "找不到路径", "系统找不到指定的路径", "不是有效的 mediacrawler"),
        "MediaCrawler 路径或运行入口配置不正确，后端无法按预期启动采集程序。",
        ["在设置页检查 MediaCrawler 根目录", "确认该目录下存在 main.py 和 pyproject.toml", "修改路径后重启后端，避免旧配置仍在运行"],
        True,
    ),
    (
        "网络代理",
        ("httpx", "timeout", "timed out", "connection", "proxy", "tls", "ssl", "network", "连接"),
        "采集或 AI 请求过程中出现网络连接异常，可能是代理、目标平台限流或本地网络不稳定。",
        ["确认代理配置和网络可用", "降低并发数后重试", "如果连续失败，先用少量内容数做一次测试任务"],
        True,
    ),
    (
        "登录风控",
        ("login", "qrcode", "cookie", "验证码", "风控", "登录", "扫码"),
        "平台登录态不可用或触发风控，采集无法继续获取数据。",
        ["重新完成平台登录", "降低采集频率和并发", "先运行小任务验证登录态是否恢复"],
        True,
    ),
    (
        "AI配置",
        ("api key", "base url", "模型名", "ai base", "ai_api_key", "openai 兼容"),
        "AI 接口配置缺失或不可用，分析任务无法请求模型。",
        ["在设置页填写 OpenAI 兼容 Base URL、API Key 和 Model", "用一个单条 AI 分析任务测试配置", "确认模型接口返回 chat/completions 兼容格式"],
        True,
    ),
    (
        "AI输出解析",
        ("json", "parse", "decode", "输出中没有找到json", "解析失败"),
        "AI 返回内容不是合法 JSON，系统没有写入兜底伪结果。",
        ["查看 AI 分析页的原始输出和提示词", "降低模型温度或更换更稳定的模型", "重试失败任务，确认模型按 JSON 格式输出"],
        True,
    ),
    (
        "导入失败",
        ("归一化导入失败", "导入失败", "import failed", "normaliz"),
        "采集完成后导入项目库失败，可能是底层字段结构变化或数据异常。",
        ["查看日志中导入失败前后的字段信息", "确认当前 MediaCrawler 版本和数据库表结构", "保留失败日志后再调整导入映射"],
        True,
    ),
    (
        "任务中断",
        ("interrupted", "backend restart", "任务被后端恢复", "cancelled", "已取消", "terminated"),
        "任务被取消或后端重启中断，需要按当前数据状态决定是否重跑。",
        ["如果任务未入库，可直接删除任务记录", "如果已有部分数据，先查看防重复与删除记录", "需要继续采集时重新创建任务"],
        True,
    ),
]


def task_diagnostics(task_id: str) -> dict[str, Any]:
    task = crawler_adapter.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    logs = crawler_adapter.list_task_logs(task_id)
    status = str(task.get("status") or "")
    outcome = task.get("outcome") if isinstance(task.get("outcome"), dict) else {}
    counts = outcome.get("counts") if isinstance(outcome.get("counts"), dict) else {}

    if status in ("pending", "running"):
        return {
            "status": "ok",
            "category": "未知",
            "summary": "任务仍在排队或运行中，等待任务结束后再生成诊断。",
            "evidence": [_counts_evidence(counts)],
            "next_steps": ["任务结束后刷新任务日志页面", "如果长时间无新日志，再检查浏览器登录态和网络连接"],
            "retryable": False,
        }

    if status == "succeeded" and _business_total(counts) == 0:
        return {
            "status": "warning",
            "category": "无有效数据",
            "summary": "任务成功结束，但没有导入有效业务数据。",
            "evidence": [_counts_evidence(counts)],
            "next_steps": ["检查关键词/主页/ID 是否正确", "检查设置页的平台诊断和采集日期窗口", "确认 MediaCrawler 底层库路径不是空库或旧库"],
            "retryable": True,
        }

    text = _diagnostic_text(task, logs)
    if status == "failed":
        for category, tokens, summary, next_steps, retryable in DIAGNOSTIC_RULES:
            if _contains_any(text, tokens):
                return {
                    "status": "failed",
                    "category": category,
                    "summary": summary,
                    "evidence": _matched_evidence(task, logs, tokens, counts),
                    "next_steps": next_steps,
                    "retryable": retryable,
                }
        return {
            "status": "failed",
            "category": "未知",
            "summary": "任务失败，但日志没有命中明确诊断规则。",
            "evidence": _matched_evidence(task, logs, tuple(), counts),
            "next_steps": ["查看最后 20 行日志定位异常", "先用相同参数的小任务复现", "如果错误稳定出现，再根据完整 traceback 调整对应模块"],
            "retryable": True,
        }

    return {
        "status": "ok",
        "category": "无异常",
        "summary": "任务已完成，并且导入了可用业务数据。",
        "evidence": [_counts_evidence(counts)],
        "next_steps": list(outcome.get("next_actions") or ["进入数据表或总览树查看结果"]),
        "retryable": False,
    }


def _diagnostic_text(task: dict[str, Any], logs: list[dict[str, Any]]) -> str:
    parts = [str(task.get("error") or ""), str(task.get("command") or "")]
    parts.extend(str(item.get("message") or "") for item in logs[-80:])
    with database.connect() as conn:
        media_path = database.get_setting(conn, "media_crawler_path", "")
    if media_path and not Path(media_path).exists():
        parts.append("media_crawler_path no such file")
    return "\n".join(parts).lower()


def _matched_evidence(
    task: dict[str, Any],
    logs: list[dict[str, Any]],
    tokens: tuple[str, ...],
    counts: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    error = str(task.get("error") or "").strip()
    if error:
        evidence.append({"source": "task_error", "message": error[:500]})
    lowered_tokens = tuple(token.lower() for token in tokens)
    for item in logs:
        message = str(item.get("message") or "")
        if not lowered_tokens or _contains_any(message.lower(), lowered_tokens):
            evidence.append(
                {
                    "source": "task_log",
                    "message": message[:500],
                    "created_at": item.get("created_at") or "",
                    "level": item.get("level") or "",
                }
            )
        if len(evidence) >= 6:
            break
    evidence.append(_counts_evidence(counts))
    return evidence


def _counts_evidence(counts: dict[str, Any]) -> dict[str, Any]:
    return {"source": "task_outcome", "message": "任务入库统计", "counts": dict(counts or {})}


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token.lower() in text for token in tokens)


def _business_total(counts: dict[str, Any]) -> int:
    return sum(int(counts.get(key) or 0) for key in ("contents", "comments", "competitor_candidates", "leads"))
