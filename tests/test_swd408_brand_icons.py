"""Brand mark assets for the App store, Ingress, and the thin HA integration."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "heating_assistant"
APP_DIR = ROOT / "heating_assistant"
STATIC = ROOT / "heatingassistant" / "app" / "static"
SYNC = ROOT / "scripts" / "sync-ha-app-package.sh"


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_icon_svg_is_the_shared_source() -> None:
    svg = (INTEGRATION / "icon.svg").read_text(encoding="utf-8")
    static_svg = (STATIC / "img" / "logo.svg").read_text(encoding="utf-8")
    assert svg == static_svg
    assert 'viewBox="0 0 100 100"' in svg
    assert "#00d4aa" in svg
    assert "M 20 84 L 20 47 L 50 19 L 80 47 L 80 84 Z" in svg
    icons_js = (STATIC / "heating-assistant-icons.js").read_text(encoding="utf-8")
    assert "heating-assistant:logo" in icons_js
    assert "M 20 84 L 20 47 L 50 19 L 80 47 L 80 84 Z" in icons_js


def test_supervisor_app_icon_and_logo_sizes() -> None:
    assert png_size(APP_DIR / "icon.png") == (128, 128)
    width, height = png_size(APP_DIR / "logo.png")
    assert width == 500
    assert height == 200
    assert width > height


def test_integration_brand_folder_matches_ha_2026_3() -> None:
    brand = INTEGRATION / "brand"
    assert png_size(brand / "icon.png") == (256, 256)
    assert png_size(brand / "icon@2x.png") == (512, 512)
    assert png_size(brand / "dark_icon.png") == (256, 256)
    assert png_size(brand / "dark_icon@2x.png") == (512, 512)
    logo_w, logo_h = png_size(brand / "logo.png")
    assert logo_w > logo_h
    assert png_size(brand / "logo.png") == png_size(brand / "dark_logo.png")
    assert png_size(brand / "logo@2x.png") == png_size(brand / "dark_logo@2x.png")


def test_brands_lift_copy_matches_readme() -> None:
    lift = ROOT / "brands" / "custom_integrations" / "heating_assistant"
    assert png_size(lift / "icon.png") == (256, 256)
    assert png_size(lift / "icon@2x.png") == (512, 512)
    assert (lift / "icon.png").read_bytes() == (INTEGRATION / "brand" / "icon.png").read_bytes()


def test_ingress_uses_shared_svg_and_favicon() -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    dashboard = (STATIC / "industrial-dashboard.js").read_text(encoding="utf-8")
    assert 'rel="icon" href="static/img/logo.svg?v=136"' in index
    assert 'rel="apple-touch-icon" href="static/img/favicon.png?v=136"' in index
    assert png_size(STATIC / "img" / "favicon.png") == (128, 128)
    assert "img/logo.svg" in dashboard
    assert "panel-nav__logo" in dashboard
    assert 'src="${BASE_PATH}/img/logo.svg' in dashboard


def test_sync_script_copies_icon_svg_and_brand_folder() -> None:
    text = SYNC.read_text(encoding="utf-8")
    assert text.count("icon.svg") >= 2
    assert 'shutil.copytree(brand_src, dst_integration / "brand")' in text
    dest = APP_DIR / "custom_components" / "heating_assistant"
    assert (dest / "icon.svg").is_file()
    assert (dest / "brand" / "icon.png").is_file()
    assert (dest / "brand" / "icon@2x.png").is_file()
