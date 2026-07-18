from __future__ import annotations

import sqlite3
from typing import Any

from app import database
from app.services import content_publish


FEATURES = {"acquisition", "message", "traffic", "publish"}
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
    account = content_publish.create_account(platform, name)
    with database.connect() as conn:
        conn.execute("UPDATE publish_accounts SET role = ? WHERE id = ?", (role, account["id"]))
        _replace_bindings(conn, str(account["id"]), str(account["platform"]), selected, defaults)
    return get_account(str(account["id"]))


def list_accounts(*, feature: str = "", platform: str = "") -> list[dict[str, Any]]:
    accounts = content_publish.list_accounts()
    result = [_with_bindings(account) for account in accounts]
    if platform:
        result = [account for account in result if account["platform"] == platform]
    if feature:
        _validate_feature(feature)
        result = [account for account in result if feature in account["features"]]
    return result


def get_account(account_id: str, *, feature: str = "", require_ready: bool = False) -> dict[str, Any]:
    account = _with_bindings(content_publish.get_account(account_id))
    if feature:
        _validate_feature(feature)
        if feature not in account["features"]:
            raise ValueError(f"账号“{account['name']}”未启用该功能")
        binding_status = str(account["feature_status"][feature]["status"])
        if require_ready and (not account["enabled"] or account["status"] != "ready" or binding_status != "ready"):
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
    result = content_publish.delete_account(account_id)
    with database.connect() as conn:
        conn.execute(
            "UPDATE account_feature_bindings SET is_default = 0, status = 'expired', last_error = '账号已移除' WHERE account_id = ?",
            (account_id,),
        )
    return result


def resolve_account_id(platform: str, feature: str, account_id: str = "", *, require_ready: bool = True) -> str:
    _validate_feature(feature)
    if account_id:
        account = get_account(account_id, feature=feature, require_ready=require_ready)
        if account["platform"] != platform:
            raise ValueError("所选账号与任务平台不一致")
        return str(account["id"])
    candidates = [
        account for account in list_accounts(feature=feature, platform=platform)
        if feature in account["default_features"]
    ]
    if not candidates:
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
    return content_publish.account_auth_path(account_id)


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
    inherited_status = str(account_row["status"]) if account_row and account_row["status"] in {"checking", "ready", "expired", "error"} else "unknown"
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
                account_row["last_checked_at"] if account_row else None,
                str(account_row["last_error"] or "") if account_row else "",
            ),
        )
        conn.execute(
            "UPDATE account_feature_bindings SET is_default = ? WHERE account_id = ? AND feature = ?",
            (int(feature in defaults), account_id, feature),
        )


def _validate_feature(feature: str) -> None:
    if feature not in FEATURES:
        raise ValueError("未知账号功能")
