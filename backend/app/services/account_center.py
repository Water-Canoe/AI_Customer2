from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from app import database


PLATFORMS = {"dy", "ks", "xhs"}
ACCOUNT_STATUSES = {"login_required", "checking", "ready", "expired", "error"}
FEATURES = {"acquisition", "message", "traffic", "publish"}
USER_FEATURES = {"acquisition", "message", "traffic"}
LOGIN_FEATURES = {"user": USER_FEATURES, "creator": {"publish"}}
ROLE_FEATURES = {
    "brand": {"message", "publish"},
    "service": {"message"},
    "operations": {"acquisition", "message"},
    "traffic": {"acquisition", "message", "traffic"},
    "test": FEATURES,
}


def create_account(
    platform: str,
    name: str,
    role: str,
    features: list[str] | None = None,
    default_features: list[str] | None = None,
) -> dict[str, Any]:
    role, selected, defaults = _validated_bindings(role, features, default_features)
    account = _create_account_record(platform, name)
    with database.connect() as conn:
        conn.execute("UPDATE publish_accounts SET role = ? WHERE id = ?", (role, account["id"]))
        _replace_bindings(conn, str(account["id"]), str(account["platform"]), selected, defaults)
    return get_account(str(account["id"]))


def list_accounts(*, feature: str = "", platform: str = "") -> list[dict[str, Any]]:
    accounts = _list_account_records()
    result = [_with_bindings(account) for account in accounts]
    if platform:
        result = [account for account in result if account["platform"] == platform]
    if feature:
        _validate_feature(feature)
        result = [account for account in result if feature in account["features"]]
    return result


def get_account(account_id: str, *, feature: str = "", require_ready: bool = False) -> dict[str, Any]:
    account = _with_bindings(_get_account_record(account_id))
    if feature:
        _validate_feature(feature)
        if feature not in account["features"]:
            raise ValueError(f"账号“{account['name']}”未启用该功能")
        binding_status = str(account["feature_status"][feature]["status"])
        if require_ready and (not account["enabled"] or binding_status != "ready"):
            raise ValueError(f"账号“{account['name']}”未登录或已停用")
    return account


def update_account(account_id: str, values: dict[str, Any]) -> dict[str, Any]:
    current = get_account(account_id)
    name = str(values.get("name", current["name"])).strip()
    if not name:
        raise ValueError("账号名称不能为空")
    role = str(values.get("role", current["role"]))
    features = values.get("features", current["features"])
    defaults = values.get("default_features", current["default_features"])
    role, selected, defaults = _validated_bindings(role, features, defaults)
    platform_user_id = str(values.get("platform_user_id", current.get("platform_user_id", ""))).strip()
    with database.connect() as conn:
        try:
            conn.execute(
                """
                UPDATE publish_accounts
                SET name = ?, enabled = ?, role = ?, platform_user_id = ?,
                    updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (
                    name[:100],
                    int(bool(values.get("enabled", current["enabled"]))),
                    role,
                    platform_user_id,
                    account_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("同一平台下账号名称或平台账号 ID 不能重复") from exc
        _replace_bindings(conn, account_id, str(current["platform"]), selected, defaults)
    return get_account(account_id)


def delete_account(account_id: str) -> dict[str, Any]:
    account = get_account(account_id)
    with database.connect() as conn:
        active = conn.execute(
            "SELECT 1 FROM publish_tasks WHERE account_id = ? AND status IN ('waiting_media', 'queued', 'running') LIMIT 1",
            (account_id,),
        ).fetchone()
        if active:
            raise RuntimeError("该账号还有待发布或运行中的任务，暂时不能删除")
        conn.execute(
            "UPDATE publish_accounts SET deleted_at = datetime('now', 'localtime'), enabled = 0, is_default = 0, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (account_id,),
        )
        conn.execute(
            "UPDATE account_feature_bindings SET is_default = 0, status = 'expired', last_error = '账号已移除' WHERE account_id = ?",
            (account_id,),
        )
    # 账号采用持久化 Profile，软删除记录时保留登录数据，避免误删用户目录。
    return {"id": account_id, "deleted": True}


def resolve_account_id(platform: str, feature: str, account_id: str = "", *, require_ready: bool = True) -> str:
    _validate_feature(feature)
    if account_id:
        account = get_account(account_id, feature=feature, require_ready=require_ready)
        if account["platform"] != platform:
            raise ValueError("所选账号与任务平台不一致")
        return str(account["id"])
    feature_accounts = list_accounts(feature=feature, platform=platform)
    candidates = [
        account for account in feature_accounts
        if feature in account["default_features"]
    ]
    if not candidates:
        if len(feature_accounts) == 1:
            candidates = feature_accounts
        else:
            raise ValueError("该平台尚未配置此功能的默认账号，请先到账号中心设置")
    account = candidates[0]
    if require_ready:
        try:
            get_account(str(account["id"]), feature=feature, require_ready=True)
        except ValueError as exc:
            raise ValueError(f"默认账号“{account['name']}”未登录或已停用") from exc
    return str(account["id"])


def profile_path(account_id: str, feature: str = "", *, require_ready: bool = True):
    if feature:
        get_account(account_id, feature=feature, require_ready=require_ready)
    login_kind = "creator" if feature == "publish" else "user"
    return account_auth_path(account_id, login_kind)


def account_auth_path(account_id: str, login_kind: str = "creator") -> Path:
    _get_account_record(account_id, include_deleted=True)
    if login_kind not in {"user", "creator"}:
        raise ValueError("未知登录类型")
    creator_path = _resolve_runtime_path(_auth_relative_path(account_id))
    path = creator_path if login_kind == "creator" else creator_path.parent / "user-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def account_qrcode_path(account_id: str) -> Path:
    with database.connect() as conn:
        row = conn.execute("SELECT qrcode_relative_path FROM publish_accounts WHERE id = ?", (account_id,)).fetchone()
    if not row or not row["qrcode_relative_path"]:
        raise ValueError("登录二维码尚未生成")
    path = _resolve_runtime_path(str(row["qrcode_relative_path"]))
    if not path.is_file():
        raise ValueError("登录二维码不存在或已失效")
    return path


def require_login_kind(account_id: str, login_kind: str) -> dict[str, Any]:
    features = _login_features(login_kind)
    account = get_account(account_id)
    if not features.intersection(account["features"]):
        label = "创作者登录" if login_kind == "creator" else "用户登录"
        raise ValueError(f"该账号没有启用需要{label}的功能")
    return account


def set_login_status(account_id: str, login_kind: str, status: str, error: str = "", *, checked: bool = False) -> None:
    features = _login_features(login_kind)
    require_login_kind(account_id, login_kind)
    if login_kind == "creator":
        set_account_state(account_id, status, error=error, checked=checked)
    placeholders = ",".join("?" for _ in features)
    checked_sql = ", last_checked_at = datetime('now', 'localtime')" if checked else ""
    with database.connect() as conn:
        conn.execute(
            f"""
            UPDATE account_feature_bindings
            SET status = ?, last_error = ?{checked_sql}
            WHERE account_id = ? AND feature IN ({placeholders})
            """,
            (status, error, account_id, *sorted(features)),
        )


def set_account_state(
    account_id: str,
    status: str,
    *,
    error: str = "",
    qrcode_relative_path: str = "",
    checked: bool = False,
) -> dict[str, Any]:
    if status not in ACCOUNT_STATUSES:
        raise ValueError("未知账号状态")
    _get_account_record(account_id, include_deleted=True)
    checked_sql = ", last_checked_at = datetime('now', 'localtime')" if checked else ""
    with database.connect() as conn:
        conn.execute(
            f"""
            UPDATE publish_accounts
            SET status = ?, last_error = ?, qrcode_relative_path = ?,
                updated_at = datetime('now', 'localtime'){checked_sql}
            WHERE id = ?
            """,
            (status, str(error or ""), str(qrcode_relative_path or ""), account_id),
        )
    return _get_account_record(account_id, include_deleted=True)


def set_feature_status(account_id: str, feature: str, status: str, error: str = "") -> None:
    _validate_feature(feature)
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE account_feature_bindings
            SET status = ?, last_error = ?, last_checked_at = datetime('now', 'localtime')
            WHERE account_id = ? AND feature = ?
            """,
            (status, error, account_id, feature),
        )


def set_all_feature_status(account_id: str, status: str, error: str = "") -> None:
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE account_feature_bindings
            SET status = ?, last_error = ?, last_checked_at = datetime('now', 'localtime')
            WHERE account_id = ?
            """,
            (status, error, account_id),
        )


def _create_account_record(platform: str, name: str) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    name = str(name or "").strip()
    if platform not in PLATFORMS:
        raise ValueError("发布平台只支持抖音、快手和小红书")
    if not name:
        raise ValueError("账号名称不能为空")
    account_id = uuid.uuid4().hex
    try:
        with database.connect() as conn:
            conn.execute(
                "INSERT INTO publish_accounts(id, platform, name, auth_relative_path) VALUES(?, ?, ?, ?)",
                (account_id, platform, name[:100], f"platform_accounts/{platform}/{account_id}/creator-profile"),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("同一平台下账号名称不能重复") from exc
    return _get_account_record(account_id)


def _list_account_records(*, include_deleted: bool = False) -> list[dict[str, Any]]:
    where = "1 = 1" if include_deleted else "deleted_at IS NULL"
    with database.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM publish_accounts WHERE {where} ORDER BY platform, is_default DESC, created_at DESC"
        ).fetchall()
    return [_format_account(row) for row in rows]


def _get_account_record(account_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM publish_accounts WHERE id = ?", (str(account_id),)).fetchone()
    if not row or (row["deleted_at"] and not include_deleted):
        raise ValueError("平台账号不存在")
    return _format_account(row)


def _resolve_runtime_path(relative_path: str) -> Path:
    root = database.get_data_root().resolve()
    path = (root / str(relative_path or "")).resolve()
    if path == root or root not in path.parents:
        raise ValueError("账号运行路径超出数据目录")
    return path


def _auth_relative_path(account_id: str) -> str:
    with database.connect() as conn:
        row = conn.execute("SELECT auth_relative_path FROM publish_accounts WHERE id = ?", (account_id,)).fetchone()
    if not row:
        raise ValueError("平台账号不存在")
    return str(row["auth_relative_path"])


def _format_account(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value.pop("auth_relative_path", None)
    value["enabled"] = bool(value.get("enabled"))
    value["is_default"] = bool(value.get("is_default"))
    value["qrcode_url"] = f"/api/accounts/{value['id']}/qrcode" if value.get("qrcode_relative_path") else ""
    return value


def _with_bindings(account: dict[str, Any]) -> dict[str, Any]:
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT feature, is_default, status, last_checked_at, last_error FROM account_feature_bindings WHERE account_id = ? ORDER BY feature",
            (str(account["id"]),),
        ).fetchall()
    bindings = database.rows_to_dicts(rows)
    result = dict(account)
    result["features"] = [str(row["feature"]) for row in bindings]
    result["default_features"] = [str(row["feature"]) for row in bindings if bool(row["is_default"])]
    result["feature_status"] = {str(row["feature"]): row for row in bindings}
    result["login_status"] = {
        login_kind: _scope_status(bindings, features)
        for login_kind, features in LOGIN_FEATURES.items()
    }
    return result


def _validated_bindings(
    role: str,
    features: list[str] | None,
    default_features: list[str] | None,
) -> tuple[str, set[str], set[str]]:
    role = str(role or "").strip()
    if role not in ROLE_FEATURES:
        raise ValueError("未知账号角色")
    selected_values = ROLE_FEATURES[role] if features is None else features
    selected = {str(value) for value in selected_values}
    defaults = {str(value) for value in (default_features or [])}
    if not selected <= ROLE_FEATURES[role]:
        raise ValueError("账号角色不允许绑定所选功能")
    if not defaults <= selected:
        raise ValueError("默认功能必须先绑定到账号")
    return role, selected, defaults


def _replace_bindings(conn: Any, account_id: str, platform: str, features: set[str], defaults: set[str]) -> None:
    existing_rows = conn.execute(
        "SELECT feature, status, last_checked_at, last_error FROM account_feature_bindings WHERE account_id = ?",
        (account_id,),
    ).fetchall()
    existing_bindings = database.rows_to_dicts(existing_rows)
    if features:
        placeholders = ",".join("?" for _ in features)
        conn.execute(
            f"DELETE FROM account_feature_bindings WHERE account_id = ? AND feature NOT IN ({placeholders})",
            (account_id, *sorted(features)),
        )
    else:
        conn.execute("DELETE FROM account_feature_bindings WHERE account_id = ?", (account_id,))
    account_row = conn.execute(
        "SELECT status, last_checked_at, last_error FROM publish_accounts WHERE id = ?",
        (account_id,),
    ).fetchone()
    for feature in sorted(features):
        if feature in defaults:
            conn.execute(
                """
                UPDATE account_feature_bindings
                SET is_default = 0
                WHERE feature = ? AND account_id IN (
                    SELECT id FROM publish_accounts WHERE platform = ? AND deleted_at IS NULL
                )
                """,
                (feature, platform),
            )
        scope = _scope_status(existing_bindings, {"publish"} if feature == "publish" else USER_FEATURES)
        inherited_status = str(scope["status"])
        if inherited_status == "unknown" and feature == "publish" and account_row:
            inherited_status = str(account_row["status"]) if account_row["status"] in {"checking", "ready", "expired", "error"} else "unknown"
        conn.execute(
            """
            INSERT OR IGNORE INTO account_feature_bindings(
                account_id, feature, status, last_checked_at, last_error
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                account_id,
                feature,
                inherited_status,
                scope["last_checked_at"] or (account_row["last_checked_at"] if feature == "publish" and account_row else None),
                str(scope["last_error"] or (account_row["last_error"] if feature == "publish" and account_row else "")),
            ),
        )
        conn.execute(
            "UPDATE account_feature_bindings SET is_default = ? WHERE account_id = ? AND feature = ?",
            (int(feature in defaults), account_id, feature),
        )


def _validate_feature(feature: str) -> None:
    if feature not in FEATURES:
        raise ValueError("未知账号功能")


def _login_features(login_kind: str) -> set[str]:
    if login_kind not in LOGIN_FEATURES:
        raise ValueError("未知登录类型")
    return LOGIN_FEATURES[login_kind]


def _scope_status(bindings: list[dict[str, Any]], features: set[str]) -> dict[str, Any]:
    rows = [row for row in bindings if str(row["feature"]) in features]
    if not rows:
        return {"available": False, "status": "unknown", "last_checked_at": None, "last_error": ""}
    statuses = {str(row["status"] or "unknown") for row in rows}
    if "checking" in statuses:
        status = "checking"
    elif statuses == {"ready"}:
        status = "ready"
    elif "error" in statuses:
        status = "error"
    elif "expired" in statuses:
        status = "expired"
    else:
        status = "unknown"
    errors = [str(row["last_error"] or "") for row in rows if row["last_error"]]
    checked = [str(row["last_checked_at"]) for row in rows if row["last_checked_at"]]
    return {
        "available": True,
        "status": status,
        "last_checked_at": max(checked) if checked else None,
        "last_error": errors[0] if errors else "",
    }
