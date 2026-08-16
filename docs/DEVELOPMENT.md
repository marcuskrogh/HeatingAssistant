# Development

Architecture and packaging notes for Heating Assistant as a **Home Assistant OS
App** with a **thin MQTT bridge** integration.

## Architecture

```text
Home Assistant OS
├── Mosquitto (MQTT)
├── HeatingAssistant App          compute, config, history, Ingress UI
│   └── heatingassistant/         Python package (engine + app runtime)
└── custom_components/
    └── heating_assistant/        thin bridge (entity catalog + MQTT tags)
```

| Piece | Owns |
|-------|------|
| **App** (`heatingassistant/`) | MPC/EKF, schedules, identification, durable data, Ingress panel, synthetic panel state |
| **Thin integration** | Subscribe/publish HA entity values ↔ MQTT; config flow is **instance ID** only |
| **MQTT** | Sole data plane between App and HA entities |

The App syncs the bundled thin integration into Home Assistant’s config share on
start. Restart Home Assistant Core after sync so Core loads the copy.

## Repository layout

```text
heating_assistant/          HAOS App packaging (config.yaml, Dockerfile, run.sh)
heatingassistant/           App runtime + control engine + Ingress static UI
custom_components/
  heating_assistant/        Thin MQTT bridge (source; synced into App image)
docs/                       User and developer docs
scripts/sync-ha-app-package.sh
tests/
```

Root `README.md` is canonical; `scripts/sync-ha-app-package.sh` copies it into
`heating_assistant/README.md` for the App build context.

## Versioning

Calendar versions **`YYYY.MM.PATCH`** (Home Assistant–style), e.g. `2026.08.0`.

- First release in a calendar month starts at patch **`0`**.
- Later releases in the same month increment the patch.

Keep the lock in sync, then run the sync script:

1. `heating_assistant/config.yaml` → `version`
2. `heating_assistant/Dockerfile` → `BUILD_VERSION`
3. Root `pyproject.toml` → `project.version`
4. `heating_assistant/CHANGELOG.md` → `# YYYY.MM.PATCH` heading (Supervisor
   update dialog; ship appends here)
5. `scripts/sync-ha-app-package.sh` (aligns integration / `__version__` strings)

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .
pytest
```

App packaging guards live in `tests/test_github_app_repo.py` and related calver
tests. MQTT topic conventions for the bridge are documented in
`docs/agents/MQTT-TOPICS.md`.

## Product vs packaging surfaces

- **Product / consumer docs** — README, Configuration, Tuning, Theory: no issue
  tracker keys, no HACS install path.
- **Dev surfaces** — tracker, `docs/agents/*`, PR/branch names: issue keys OK.

## Related

- [Theory](THEORY.md)
- [Configuration](CONFIGURATION.md)
- [Tuning](TUNING.md)
