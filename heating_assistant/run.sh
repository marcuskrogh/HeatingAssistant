#!/usr/bin/env sh
# HeatingAssistant HA App entry (SWD-255 / SWD-258).
# Supervisor mounts persistent storage at /data and HA config at /homeassistant.
set -eu

DATA_DIR="${HEATINGASSISTANT_DATA:-/data}"
OPTIONS_PATH="${DATA_DIR}/options.json"
HOST="${HEATINGASSISTANT_HOST:-0.0.0.0}"
PORT="${HEATINGASSISTANT_PORT:-8099}"
HA_CONFIG="${HEATINGASSISTANT_HA_CONFIG:-/homeassistant}"
INTEGRATION_SRC="${HEATINGASSISTANT_INTEGRATION_SRC:-/usr/share/heatingassistant/custom_components/heating_assistant}"
INTEGRATION_DST="${HA_CONFIG}/custom_components/heating_assistant"
APP_VERSION_FILE="${HEATINGASSISTANT_APP_VERSION_FILE:-/usr/share/heatingassistant/APP_VERSION}"
INTEGRATION_STAMP="${DATA_DIR}/bundled_integration_from_app"

mkdir -p "${DATA_DIR}"

# Supervisor writes App options here; seed defaults when missing so the runtime
# has the MQTT connection settings expected by the HAOS App skeleton.
if [ ! -f "${OPTIONS_PATH}" ]; then
  cat > "${OPTIONS_PATH}" <<'EOF'
{
  "instance_id": "default",
  "mqtt_broker": "core-mosquitto",
  "mqtt_port": 1883,
  "mqtt_username": "",
  "mqtt_password": ""
}
EOF
  echo "HeatingAssistant: seeded default ${OPTIONS_PATH} (mqtt_broker=core-mosquitto)"
fi

app_version() {
  if [ -f "${APP_VERSION_FILE}" ]; then
    tr -d '[:space:]' < "${APP_VERSION_FILE}"
  else
    printf '%s\n' "unknown"
  fi
}

notify_core_restart_needed() {
  if [ -z "${SUPERVISOR_TOKEN:-}" ]; then
    return 1
  fi
  curl -fsS -X POST \
    -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"notification_id":"heatingassistant_core_restart","title":"HeatingAssistant needs Core restart","message":"Bundled integration updated. Restart Home Assistant Core so HeatingAssistant can load the synced integration."}' \
    http://supervisor/core/api/services/persistent_notification/create
}

request_core_restart_after_sync() {
  auto="${HEATINGASSISTANT_AUTO_CORE_RESTART:-1}"
  case "${auto}" in
    0|false|no|NO|False)
      echo "HeatingAssistant: auto Core restart disabled (HEATINGASSISTANT_AUTO_CORE_RESTART=${auto}); restart Core manually so the synced integration loads."
      notify_core_restart_needed || true
      return 0
      ;;
  esac
  if [ -z "${SUPERVISOR_TOKEN:-}" ]; then
    echo "HeatingAssistant: SUPERVISOR_TOKEN missing; cannot auto-restart Core. Restart Home Assistant Core manually so the synced integration loads." >&2
    return 0
  fi
  echo "HeatingAssistant: requesting Home Assistant Core restart so the synced integration loads..."
  if curl -fsS -X POST \
    -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
    -H "Content-Type: application/json" \
    http://supervisor/core/restart; then
    echo "HeatingAssistant: Core restart requested."
  else
    echo "HeatingAssistant: Core restart request failed; creating HA notification." >&2
    notify_core_restart_needed || \
      echo "HeatingAssistant: could not create persistent notification either; restart Core manually." >&2
  fi
  return 0
}

integration_up_to_date() {
  [ -d "${INTEGRATION_DST}" ] || return 1
  python3 - "${INTEGRATION_SRC}" "${INTEGRATION_DST}" <<'PY'
import sys
from pathlib import Path


def files(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        out[str(path.relative_to(root))] = path.read_bytes()
    return out


sys.exit(0 if files(Path(sys.argv[1])) == files(Path(sys.argv[2])) else 1)
PY
}

needs_integration_sync() {
  ver="$(app_version)"
  if [ ! -f "${INTEGRATION_STAMP}" ] || [ "$(cat "${INTEGRATION_STAMP}")" != "${ver}" ]; then
    echo "HeatingAssistant: App version stamp ${ver} requires integration sync."
    return 0
  fi
  if ! integration_up_to_date; then
    return 0
  fi
  return 1
}

install_thin_integration() {
  if [ ! -d "${HA_CONFIG}" ]; then
    echo "HeatingAssistant: HA config not mounted at ${HA_CONFIG}; skip integration install."
    return 0
  fi
  if [ ! -d "${INTEGRATION_SRC}" ]; then
    echo "HeatingAssistant: bundled integration missing at ${INTEGRATION_SRC}; skip install."
    return 0
  fi

  if ! needs_integration_sync; then
    echo "HeatingAssistant: integration already up to date at ${INTEGRATION_DST}"
    rm -f "${DATA_DIR}/integration_needs_core_restart"
    return 0
  fi

  mkdir -p "${HA_CONFIG}/custom_components" || {
    echo "HeatingAssistant: failed to create ${HA_CONFIG}/custom_components" >&2
    return 1
  }

  tmp="${INTEGRATION_DST}.new"
  bak="${INTEGRATION_DST}.bak"
  rm -rf "${tmp}" "${bak}"
  mkdir -p "${tmp}" || {
    echo "HeatingAssistant: failed to create staging dir ${tmp}" >&2
    return 1
  }
  cp -a "${INTEGRATION_SRC}/." "${tmp}/" || {
    echo "HeatingAssistant: failed to stage integration from ${INTEGRATION_SRC}" >&2
    rm -rf "${tmp}"
    return 1
  }
  [ -f "${tmp}/manifest.json" ] || {
    echo "HeatingAssistant: staged integration missing manifest.json" >&2
    rm -rf "${tmp}"
    return 1
  }

  if [ -e "${INTEGRATION_DST}" ]; then
    mv "${INTEGRATION_DST}" "${bak}" || {
      echo "HeatingAssistant: failed to move existing integration aside" >&2
      rm -rf "${tmp}"
      return 1
    }
  fi
  if ! mv "${tmp}" "${INTEGRATION_DST}"; then
    echo "HeatingAssistant: failed to activate staged integration at ${INTEGRATION_DST}" >&2
    rm -rf "${tmp}"
    if [ -e "${bak}" ]; then
      mv "${bak}" "${INTEGRATION_DST}" || \
        echo "HeatingAssistant: also failed to restore previous integration" >&2
    fi
    return 1
  fi
  rm -rf "${bak}" "${INTEGRATION_DST}/__pycache__"

  echo "HeatingAssistant: integration installed/updated at ${INTEGRATION_DST}"
  echo "HeatingAssistant: Restart Home Assistant Core, then add HeatingAssistant under Devices & services when the thin integration is ready."
  printf '%s\n' "$(app_version)" > "${INTEGRATION_STAMP}" || \
    echo "HeatingAssistant: warning: could not write ${INTEGRATION_STAMP}" >&2
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "${DATA_DIR}/integration_needs_core_restart" || \
    echo "HeatingAssistant: warning: could not write integration_needs_core_restart" >&2
  request_core_restart_after_sync || true
  return 0
}

# Never abort App start on integration copy failure. A sync issue should not
# cause Supervisor restart loops while the runtime can still expose diagnostics.
set +e
install_thin_integration
install_rc=$?
set -e
if [ "${install_rc}" -ne 0 ]; then
  echo "HeatingAssistant: integration install failed; continuing App start." >&2
fi

exec python3 -m heatingassistant.app \
  --host "${HOST}" \
  --port "${PORT}" \
  --options-path "${OPTIONS_PATH}" \
  --data-dir "${DATA_DIR}" \
  --ha-runtime
