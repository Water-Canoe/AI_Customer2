from pathlib import Path


PAGE = Path(__file__).parents[2] / "tools" / "license-admin.html"


def test_license_admin_page_has_safe_complete_management_actions() -> None:
    # 管理页必须支持完整授权管理，同时避免持久化或不安全渲染管理凭证。
    source = PAGE.read_text(encoding="utf-8")

    assert "/admin/licenses/${item.licenseId}/devices/${encodeURIComponent(deviceId)}/restore" in source
    assert "licenseStatusFilter" in source
    assert "deviceStatusFilter" in source
    assert "exportLicenses" in source
    assert "saveSelected" in source
    assert "TRUSTED_API_HOSTS" in source
    assert "innerHTML" not in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source


def test_license_admin_page_prioritizes_daily_management_workflow() -> None:
    # 列表和风险状态应优先展示，低频连接、创建与编辑操作收进原生弹窗。
    source = PAGE.read_text(encoding="utf-8")

    assert source.index('id="licenseRows"') < source.index('id="createDialog"')
    assert source.count("<dialog") == 3
    assert 'id="statExpiring"' in source
    assert 'data-license-filter="expiring"' in source
    assert "function isExpiringSoon(item)" in source
