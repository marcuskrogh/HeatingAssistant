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
THIN_FILES=(
  manifest.json
  __init__.py
  const.py
  mqtt_topics.py
  forecast_publish.py
  config_flow.py
  version_sync.py
  update.py
  strings.json
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
# App subtree docs live at ../docs relative to heating_assistant/README.md
sed -i 's|](docs/|](../docs/|g; s|](LICENSE)|](../LICENSE)|g' "${APP_DIR}/README.md"

python3 - "${ROOT}" <<'PY'
from __future__ import annotations

import json
import re
import shutil
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


CALVER_RE = re.compile(r"^(\d{4})\.(\d{2})\.(0|[1-9]\d*)$")


def require_calver(version: str, label: str) -> str:
    match = CALVER_RE.fullmatch(version)
    if not match:
        raise SystemExit(
            f"{label} must be YYYY.MM.PATCH (zero-padded month, unpadded patch); "
            f"got {version!r}"
        )
    month = int(match.group(2))
    if month < 1 or month > 12:
        raise SystemExit(f"{label} month must be 01–12; got {version!r}")
    return version


config_version = require_calver(
    yaml_scalar(config_text, "version"),
    "heating_assistant/config.yaml version",
)
if yaml_scalar(config_text, "slug") != "heatingassistant":
    raise SystemExit("heating_assistant/config.yaml slug must be heatingassistant")

# Keep source-of-truth package + thin integration versions aligned before copy.
src_const_path = root / "custom_components" / "heating_assistant" / "const.py"
src_manifest_path = root / "custom_components" / "heating_assistant" / "manifest.json"
src_package_init = root / "heatingassistant" / "__init__.py"

src_const = src_const_path.read_text(encoding="utf-8")
src_const = re.sub(
    r'^VERSION = ".*"$',
    f'VERSION = "{config_version}"',
    src_const,
    flags=re.MULTILINE,
)
src_const_path.write_text(src_const, encoding="utf-8")

src_manifest = json.loads(src_manifest_path.read_text(encoding="utf-8"))
src_manifest["version"] = config_version
src_manifest_path.write_text(
    json.dumps(src_manifest, indent=2, ensure_ascii=True) + "\n",
    encoding="utf-8",
)

pkg_init = src_package_init.read_text(encoding="utf-8")
pkg_init = re.sub(
    r'^__version__ = ".*"$',
    f'__version__ = "{config_version}"',
    pkg_init,
    flags=re.MULTILINE,
)
src_package_init.write_text(pkg_init, encoding="utf-8")

# Re-copy after patching sources so App context picks up aligned versions.
dst_package = app_dir / "heatingassistant"
dst_integration = app_dir / "custom_components" / "heating_assistant"
shutil.rmtree(dst_package, ignore_errors=True)
shutil.rmtree(dst_integration, ignore_errors=True)
dst_integration.mkdir(parents=True, exist_ok=True)
shutil.copytree(root / "heatingassistant", dst_package)
for name in (
    "manifest.json",
    "__init__.py",
    "const.py",
    "mqtt_topics.py",
    "forecast_publish.py",
    "config_flow.py",
    "version_sync.py",
    "update.py",
    "strings.json",
):
    shutil.copy2(
        root / "custom_components" / "heating_assistant" / name,
        dst_integration / name,
    )
shutil.copy2(root / "pyproject.toml", app_dir / "pyproject.toml")
shutil.copy2(root / "README.md", app_dir / "README.md")
readme_app = (app_dir / "README.md").read_text(encoding="utf-8")
readme_app = readme_app.replace("](docs/", "](../docs/").replace("](LICENSE)", "](../LICENSE)")
(app_dir / "README.md").write_text(readme_app, encoding="utf-8")

dockerfile = dockerfile_path.read_text(encoding="utf-8")
match = re.search(r"^ARG BUILD_VERSION=([^\s]+)$", dockerfile, flags=re.MULTILINE)
if not match:
    raise SystemExit("heating_assistant/Dockerfile missing ARG BUILD_VERSION")
docker_version = require_calver(
    match.group(1).strip('"'),
    "heating_assistant/Dockerfile BUILD_VERSION",
)

project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]
project_version = require_calver(str(project["version"]), "pyproject.toml project.version")
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

pkg_version_match = re.search(
    r'^__version__ = "([^"]+)"$',
    (app_dir / "heatingassistant" / "__init__.py").read_text(encoding="utf-8"),
    flags=re.MULTILINE,
)
if not pkg_version_match:
    raise SystemExit("heatingassistant/__init__.py missing __version__")
package_version = require_calver(
    pkg_version_match.group(1),
    "heatingassistant.__version__",
)

versions = {
    "heating_assistant/config.yaml": config_version,
    "heating_assistant/Dockerfile BUILD_VERSION": docker_version,
    "pyproject.toml project.version": project_version,
    "heating_assistant/custom_components/heating_assistant/manifest.json": manifest[
        "version"
    ],
    "heatingassistant.__version__": package_version,
}
if len(set(versions.values())) != 1:
    details = "\n".join(f"- {path}: {version}" for path, version in versions.items())
    raise SystemExit(f"App version lock failed:\n{details}")

print(f"Synced HeatingAssistant App build context at {app_dir.relative_to(root)}")
print(f"Version lock OK: {config_version}")
PY
