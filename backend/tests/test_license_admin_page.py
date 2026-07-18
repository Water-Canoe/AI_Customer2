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
