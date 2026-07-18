from __future__ import annotations

from pathlib import Path

import pytest


def _init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_CUSTOMER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_CUSTOMER_DB", str(tmp_path / "ai_customer.sqlite3"))
    from app import database

    database.init_db()
    return database


def test_account_center_separates_profiles_and_function_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = _init(tmp_path, monkeypatch)
    from app.services import account_center, content_publish

    brand = account_center.create_account("dy", "品牌主号", "brand", ["message", "publish"], ["publish"])
    traffic = account_center.create_account("dy", "引流号", "traffic", ["acquisition", "traffic"], ["traffic"])
    content_publish.set_account_state(brand["id"], "ready")
    content_publish.set_account_state(traffic["id"], "ready")
    account_center.set_all_feature_status(brand["id"], "ready")
    account_center.set_all_feature_status(traffic["id"], "ready")

    assert account_center.resolve_account_id("dy", "publish") == brand["id"]
    assert account_center.resolve_account_id("dy", "traffic") == traffic["id"]
    assert account_center.profile_path(brand["id"]) != account_center.profile_path(traffic["id"])
    assert database.get_data_root() in account_center.profile_path(brand["id"]).parents
    assert account_center.profile_path(brand["id"], "message") != account_center.profile_path(brand["id"], "publish")


def test_brand_account_cannot_bind_high_risk_features(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init(tmp_path, monkeypatch)
    from app.services import account_center

    with pytest.raises(ValueError, match="不允许"):
        account_center.create_account("dy", "品牌主号", "brand", ["traffic"])


def test_changing_default_account_clears_previous_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init(tmp_path, monkeypatch)
    from app.services import account_center

    first = account_center.create_account("dy", "客服一号", "service", ["message"], ["message"])
    second = account_center.create_account("dy", "客服二号", "service", ["message"], ["message"])
    accounts = account_center.list_accounts(feature="message", platform="dy")

    assert next(account for account in accounts if account["id"] == first["id"])["default_features"] == []
    assert next(account for account in accounts if account["id"] == second["id"])["default_features"] == ["message"]


def test_account_can_remove_all_feature_bindings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init(tmp_path, monkeypatch)
    from app.services import account_center

    account = account_center.create_account("dy", "暂不执行账号", "test", ["message"], ["message"])
    updated = account_center.update_account(account["id"], {"features": [], "default_features": []})

    assert updated["features"] == []
    assert updated["default_features"] == []


def test_new_binding_inherits_ready_login_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init(tmp_path, monkeypatch)
    from app.services import account_center, content_publish

    account = account_center.create_account("dy", "运营账号", "operations", ["message"], ["message"])
    content_publish.set_account_state(account["id"], "ready", checked=True)
    account_center.set_all_feature_status(account["id"], "ready")
    updated = account_center.update_account(account["id"], {"features": ["message", "acquisition"]})

    assert updated["feature_status"]["acquisition"]["status"] == "ready"
    assert account_center.get_account(account["id"], feature="acquisition", require_ready=True)["id"] == account["id"]


def test_feature_status_blocks_only_unavailable_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init(tmp_path, monkeypatch)
    from app.services import account_center, content_publish

    account = account_center.create_account("dy", "测试账号", "test", ["message", "traffic"], ["message", "traffic"])
    content_publish.set_account_state(account["id"], "ready", checked=True)
    account_center.set_all_feature_status(account["id"], "ready")
    account_center.set_feature_status(account["id"], "traffic", "expired", "引流登录失效")

    assert account_center.resolve_account_id("dy", "message") == account["id"]
    with pytest.raises(ValueError, match="未登录"):
        account_center.resolve_account_id("dy", "traffic")


def test_user_and_creator_login_statuses_are_independent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init(tmp_path, monkeypatch)
    from app.services import account_center

    account = account_center.create_account("dy", "品牌账号", "brand", ["message", "publish"], [])
    account_center.set_login_status(account["id"], "user", "ready", checked=True)
    account_center.set_login_status(account["id"], "creator", "expired", "创作者登录失效", checked=True)
    updated = account_center.get_account(account["id"])

    assert updated["login_status"]["user"]["status"] == "ready"
    assert updated["login_status"]["creator"]["status"] == "expired"
    assert account_center.get_account(account["id"], feature="message", require_ready=True)["id"] == account["id"]
    with pytest.raises(ValueError, match="未登录"):
        account_center.get_account(account["id"], feature="publish", require_ready=True)


def test_login_profile_migration_resets_only_user_features(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = _init(tmp_path, monkeypatch)
    from app import migrations
    from app.services import account_center, content_publish

    account = account_center.create_account("dy", "迁移账号", "test", ["message", "traffic", "publish"], [])
    account_center.set_all_feature_status(account["id"], "ready")
    content_publish.set_account_state(account["id"], "checking")
    account_center.set_feature_status(account["id"], "publish", "checking")
    with database.connect() as conn:
        migrations.MIGRATIONS[-1].action(conn, database.SCHEMA_SQL)

    updated = account_center.get_account(account["id"])
    assert updated["feature_status"]["message"]["status"] == "unknown"
    assert updated["feature_status"]["traffic"]["status"] == "unknown"
    assert updated["feature_status"]["publish"]["status"] == "expired"
    assert updated["status"] == "expired"
