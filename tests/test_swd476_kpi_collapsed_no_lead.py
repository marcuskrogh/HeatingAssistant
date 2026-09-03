"""SWD-476: collapsed KPI cards have no description lead."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TREES = (
    _ROOT / "heatingassistant" / "app" / "static",
    _ROOT / "heating_assistant" / "heatingassistant" / "app" / "static",
)


def _read(static: Path, *parts: str) -> str:
    return static.joinpath(*parts).read_text(encoding="utf-8")


def test_collapsed_host_has_no_lead() -> None:
    for static in _TREES:
        host = _read(static, "js", "components", "kpi-expand.js")
        css = _read(static, "css", "industrial.css")
        assert "kpi-expand__lead" not in host
        assert "kpi-expand__lead" not in css
        assert "kpi-expand__description-title" in host
        assert ">Description<" in host
