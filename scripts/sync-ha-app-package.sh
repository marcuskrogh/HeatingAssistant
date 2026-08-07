#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${ROOT}/heating_assistant"
CONFIG="${APP_DIR}/config.yaml"
DOCKERFILE="${APP_DIR}/Dockerfile"
SRC_PACKAGE="${ROOT}/heatingassistant"
SRC_INTEGRATION="${ROOT}/custom_components/heating_assistant_mqtt_thin"
DST_PACKAGE="${APP_DIR}/heatingassistant"
DST_INTEGRATION="${APP_DIR}/custom_components/heating_assistant"
THIN_FILES=(
  manifest.json
  __init__.py
  const.py
  mqtt_topics.py
  config_flow.py
  version_sync.py
  update.py
)

for required in "${CONFIG}" "${DOCKERFILE}" "${ROOT}/pyproject.toml" "${SRC_PACKAGE}" "${SRC_INTEGRATION}" "${ROOT}/README.md"; do
  if [ ! -e "${required}" ]; then
    echo "Missing required App packaging input: ${required}" >&2
    exit 1
  fi
done
for file in "${THIN_FILES[@]}"; do
  if [ ! -f "${SRC_INTEGRATION}/${file}" ]; then
    echo "Missing required thin integration file: ${SRC_INTEGRATION}/${file}" >&2
    exit 1
  fi
done

rm -rf "${DST_PACKAGE}" "${DST_INTEGRATION}"
mkdir -p "${APP_DIR}/custom_components" "${DST_INTEGRATION}"
cp -a "${SRC_PACKAGE}" "${DST_PACKAGE}"
for file in "${THIN_FILES[@]}"; do
  cp -a "${SRC_INTEGRATION}/${file}" "${DST_INTEGRATION}/${file}"
done
cp -a "${ROOT}/pyproject.toml" "${APP_DIR}/pyproject.toml"
cp -a "${ROOT}/README.md" "${APP_DIR}/README.md"

python3 - "${ROOT}" <<'PY'
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1])
app_dir = root / "heating_assistant"
config_path = app_dir / "config.yaml"
dockerfile_path = app_dir / "Dockerfile"
pyproject_path = root / "pyproject.toml"
manifest_path = app_dir / "custom_components" / "heating_assistant" / "manifest.json"
const_path = app_dir / "custom_components" / "heating_assistant" / "const.py"

config_paths = sorted(
    path.relative_to(root)
    for path in root.rglob("config.yaml")
    if ".git" not in path.parts and ".pytest_cache" not in path.parts
)
expected_config = Path("heating_assistant/config.yaml")
if config_paths != [expected_config]:
    raise SystemExit(
        "Expected exactly one App config.yaml at "
        f"{expected_config}, found: {', '.join(map(str, config_paths))}"
    )

config_text = config_path.read_text(encoding="utf-8")


def yaml_scalar(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*['\"]?([^'\"\n#]+)", text, flags=re.MULTILINE)
    if not match:
        raise SystemExit(f"{config_path.relative_to(root)} missing {key}")
    return match.group(1).strip()


config_version = yaml_scalar(config_text, "version")
if yaml_scalar(config_text, "slug") != "heatingassistant":
    raise SystemExit("heating_assistant/config.yaml slug must be heatingassistant")

dockerfile = dockerfile_path.read_text(encoding="utf-8")
match = re.search(r"^ARG BUILD_VERSION=([^\s]+)$", dockerfile, flags=re.MULTILINE)
if not match:
    raise SystemExit("heating_assistant/Dockerfile missing ARG BUILD_VERSION")
docker_version = match.group(1).strip('"')

project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]
project_version = str(project["version"])
project_name = str(project["name"])
if project_name != "heatingassistant":
    raise SystemExit("pyproject.toml project.name must be heatingassistant")

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
manifest["domain"] = "heating_assistant"
manifest["name"] = "Heating Assistant"
manifest["version"] = config_version
manifest_path.write_text(
    json.dumps(manifest, indent=2, ensure_ascii=True) + "\n",
    encoding="utf-8",
)

const_text = const_path.read_text(encoding="utf-8")
const_text = re.sub(r'^DOMAIN = ".*"$', 'DOMAIN = "heating_assistant"', const_text, flags=re.MULTILINE)
const_text = re.sub(r'^NAME = ".*"$', 'NAME = "Heating Assistant"', const_text, flags=re.MULTILINE)
const_text = re.sub(r'^VERSION = ".*"$', f'VERSION = "{config_version}"', const_text, flags=re.MULTILINE)
const_path.write_text(const_text, encoding="utf-8")

for forbidden in ("controller", "coordinator", "estimation", "sensor", "services"):
    if (manifest_path.parent / forbidden).exists():
        raise SystemExit(f"App-bundled thin integration contains forbidden fat directory: {forbidden}")

versions = {
    "heating_assistant/config.yaml": config_version,
    "heating_assistant/Dockerfile BUILD_VERSION": docker_version,
    "pyproject.toml project.version": project_version,
    "heating_assistant/custom_components/heating_assistant/manifest.json": manifest[
        "version"
    ],
}
if len(set(versions.values())) != 1:
    details = "\n".join(f"- {path}: {version}" for path, version in versions.items())
    raise SystemExit(f"App version lock failed:\n{details}")

print(f"Synced HeatingAssistant App build context at {app_dir.relative_to(root)}")
print(f"Version lock OK: {config_version}")
PY
