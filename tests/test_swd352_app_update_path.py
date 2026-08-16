"""SWD-352: App changelog + MQTT Restart required after thin-bridge sync."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from heatingassistant.app.core_restart import (
    DISCOVERY_OBJECT_ID,
    discovery_payload,
    discovery_topic,
    previous_version_for_stamp,
    read_stamp,
    request_core_restart,
    state_topic,
    write_stamp,
)
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "heating_assistant"
CHANGELOG = APP_DIR / "CHANGELOG.md"
APP_CONFIG = APP_DIR / "config.yaml"
APP_RUN = APP_DIR / "run.sh"


def _config_version() -> str:
    return str(yaml.safe_load(APP_CONFIG.read_text(encoding="utf-8"))["version"])


def test_changelog_has_supervisor_heading_for_current_version() -> None:
    version = _config_version()
    text = CHANGELOG.read_text(encoding="utf-8")
    pattern = re.compile(rf"^#* {re.escape(version)}\n", re.MULTILINE)
    assert pattern.search(text), f"CHANGELOG.md missing heading for {version}"
    assert "Keep a Changelog" not in text
    assert not re.search(r"^#+ \[[0-9]", text, flags=re.MULTILINE)


def test_run_sh_does_not_create_persistent_notification_or_auto_restart() -> None:
    text = APP_RUN.read_text(encoding="utf-8")
    assert "persistent_notification" not in text
    assert "http://supervisor/core/restart" not in text
    assert "HEATINGASSISTANT_AUTO_CORE_RESTART" not in text
    assert "integration_needs_core_restart" in text
    assert "from_version" in text
    assert "to_version" in text
    assert "previous_version_for_stamp" in text
    capture = text.find('from_ver="$(previous_integration_version)"')
    replace = text.find('mv "${tmp}" "${INTEGRATION_DST}"')
    skip_match = text.find("integration files already match")
    assert capture != -1
    assert replace != -1
    assert skip_match != -1
    assert capture < replace
    assert skip_match < replace


def test_previous_version_prefers_dest_manifest(tmp_path: Path) -> None:
    dest = tmp_path / "manifest.json"
    dest.write_text('{"version": "2026.08.6"}\n', encoding="utf-8")
    stamp = tmp_path / "bundled_integration_from_app"
    stamp.write_text("2026.08.7\n", encoding="utf-8")
    assert (
        previous_version_for_stamp(
            integration_stamp_path=stamp,
            dest_manifest=dest,
            to_version="2026.08.7",
        )
        == "2026.08.6"
    )


def test_previous_version_collapses_equal_from_to(tmp_path: Path) -> None:
    dest = tmp_path / "manifest.json"
    dest.write_text('{"version": "2026.08.7"}\n', encoding="utf-8")
    assert (
        previous_version_for_stamp(
            integration_stamp_path=tmp_path / "missing",
            dest_manifest=dest,
            to_version="2026.08.7",
        )
        == "previous"
    )


def test_previous_version_uses_previous_when_dest_missing(tmp_path: Path) -> None:
    assert (
        previous_version_for_stamp(
            integration_stamp_path=tmp_path / "missing",
            dest_manifest=tmp_path / "missing.json",
            to_version="2026.08.7",
        )
        == "previous"
    )


def test_stamp_round_trip(tmp_path: Path) -> None:
    path = write_stamp(tmp_path, from_version="2026.08.6", to_version="2026.08.7")
    assert path.name == "integration_needs_core_restart"
    assert read_stamp(tmp_path) == {
        "from_version": "2026.08.6",
        "to_version": "2026.08.7",
    }


def test_legacy_timestamp_stamp_is_readable(tmp_path: Path) -> None:
    (tmp_path / "integration_needs_core_restart").write_text(
        "2026-08-16T06:00:00Z\n", encoding="utf-8"
    )
    assert read_stamp(tmp_path) == {
        "from_version": "previous",
        "to_version": "2026-08-16T06:00:00Z",
    }


def test_mqtt_discovery_helpers_still_exist_for_tombstone() -> None:
    assert discovery_topic().endswith("/heatingassistant_restart/config")
    assert discovery_payload("default")["object_id"] == DISCOVERY_OBJECT_ID


@pytest.mark.asyncio
async def test_runtime_tombstones_mqtt_update_even_when_stamp_present(
    tmp_path: Path,
) -> None:
    write_stamp(tmp_path, from_version="2026.08.6", to_version="2026.08.7")
    bus = InMemoryMqttBus()
    runtime = HeatingRuntime(tmp_path, bus=bus, options={"instance_id": "default"})
    await runtime.start()
    retained = {topic: payload for topic, payload, _qos, retain in bus.published if retain}
    assert retained.get(discovery_topic()) == ""
    assert retained.get(state_topic("default")) == ""
    await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_clears_restart_discovery_when_stamp_absent(
    tmp_path: Path,
) -> None:
    bus = InMemoryMqttBus()
    runtime = HeatingRuntime(tmp_path, bus=bus, options={"instance_id": "default"})
    await runtime.start()
    retained = {topic: payload for topic, payload, _qos, retain in bus.published if retain}
    assert retained.get(discovery_topic()) == ""
    assert retained.get(state_topic("default")) == ""
    await runtime.stop()


def test_request_core_restart_skips_without_token() -> None:
    assert request_core_restart(token="") is False


def test_request_core_restart_posts_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _urlopen(request, timeout=10.0):  # noqa: ARG001
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["auth"] = request.headers.get("Authorization")
        return _Resp()

    monkeypatch.setattr("heatingassistant.app.core_restart.urlopen", _urlopen)
    assert request_core_restart(token="secret-token") is True
    assert seen["url"] == "http://supervisor/core/restart"
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer secret-token"


def test_github_app_repo_declares_changelog() -> None:
    configs = sorted(
        path.relative_to(ROOT)
        for path in ROOT.rglob("CHANGELOG.md")
        if ".git" not in path.parts and ".pytest_cache" not in path.parts
    )
    assert Path("heating_assistant/CHANGELOG.md") in configs
