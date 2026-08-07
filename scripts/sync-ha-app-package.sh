#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${ROOT}/heating_assistant"
CONFIG="${APP_DIR}/config.yaml"
DOCKERFILE="${APP_DIR}/Dockerfile"
SRC_PACKAGE="${ROOT}/heatingassistant"
SRC_INTEGRATION="${ROOT}/custom_components/heating_assistant"
DST_PACKAGE="${APP_DIR}/heatingassistant"
DST_INTEGRATION="${APP_DIR}/custom_components/heating_assistant"

for required in "${CONFIG}" "${DOCKERFILE}" "${ROOT}/pyproject.toml" "${SRC_PACKAGE}" "${SRC_INTEGRATION}" "${ROOT}/README.md"; do
  if [ ! -e "${required}" ]; then
    echo "Missing required App packaging input: ${required}" >&2
    exit 1
  fi
done

rm -rf "${DST_PACKAGE}" "${DST_INTEGRATION}"
mkdir -p "${APP_DIR}/custom_components"
cp -a "${SRC_PACKAGE}" "${DST_PACKAGE}"
cp -a "${SRC_INTEGRATION}" "${DST_INTEGRATION}"
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
manifest["version"] = config_version
manifest_path.write_text(
    json.dumps(manifest, indent=2, ensure_ascii=True) + "\n",
    encoding="utf-8",
)

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
