"""SWD-447: named ESM imports in Ingress panel JS must match module exports."""

from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


STATIC_JS = (
    Path(__file__).resolve().parents[1]
    / "heatingassistant"
    / "app"
    / "static"
    / "js"
)
NAMED_IMPORT = re.compile(
    r"import\s*\{([^}]+)\}\s*from\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)
EXPORT_DECL = re.compile(
    r"^export\s+(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)",
    re.MULTILINE,
)
EXPORT_LIST = re.compile(r"^export\s*\{([^}]+)\}", re.MULTILINE)


def _spec_names(inner: str) -> list[str]:
    names: list[str] = []
    for raw in inner.split(","):
        spec = raw.strip()
        if not spec:
            continue
        parts = re.split(r"\s+as\s+", spec)
        names.append(parts[-1].strip())
    return names


def exported_names(source: str) -> set[str]:
    names = set(EXPORT_DECL.findall(source))
    for inner in EXPORT_LIST.findall(source):
        names.update(_spec_names(inner))
    return names


def _resolve_specifier(importer: Path, specifier: str) -> Path:
    path = specifier.split("?", 1)[0]
    return (importer.parent / path).resolve()


def test_room_detail_history_imports_extend_dataset_from_room_charts() -> None:
    source = (STATIC_JS / "pages" / "room-detail-history.js").read_text(
        encoding="utf-8"
    )
    room_charts_import: list[str] | None = None
    for inner, spec in NAMED_IMPORT.findall(source):
        names = _spec_names(inner)
        if "room-charts.js" in spec:
            room_charts_import = names
        if "time-series-chart.js" in spec:
            assert "extendDatasetToNow" not in names
    assert room_charts_import is not None
    assert "extendDatasetToNow" in room_charts_import


def test_named_esm_imports_resolve_to_exports() -> None:
    missing: list[str] = []
    for path in sorted(STATIC_JS.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for inner, specifier in NAMED_IMPORT.findall(source):
            if not specifier.startswith("."):
                continue
            target = _resolve_specifier(path, specifier)
            rel_importer = path.relative_to(STATIC_JS)
            if not target.is_file():
                missing.append(f"{rel_importer}: missing module {specifier}")
                continue
            exported = exported_names(target.read_text(encoding="utf-8"))
            for name in _spec_names(inner):
                if name not in exported:
                    rel_target = target.relative_to(STATIC_JS)
                    missing.append(
                        f"{rel_importer} imports {name!r} from {rel_target}, "
                        "but that export is not found"
                    )
    assert not missing, "Named ESM import bindings missing:\n" + "\n".join(missing)


def test_dashboard_and_history_cache_bust_is_144() -> None:
    static = STATIC_JS.parent
    index = (static / "index.html").read_text(encoding="utf-8")
    dashboard = (static / "industrial-dashboard.js").read_text(encoding="utf-8")
    room = (STATIC_JS / "pages" / "room-detail.js").read_text(encoding="utf-8")
    assert "industrial-dashboard.js?v=145" in index
    assert "return '145'" in dashboard
    assert "room-detail-history.js?v=144" in room
