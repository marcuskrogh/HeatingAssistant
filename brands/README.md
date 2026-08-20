# Brand assets

Mirrors the layout of [`home-assistant/brands`](https://github.com/home-assistant/brands)
so these files can be lifted into a PR there unchanged when/if this
integration is ever published.

```
brands/
└── custom_integrations/
    └── heating_assistant/
        ├── icon.png      # 256x256 teal rounded-square badge
        └── icon@2x.png   # 512x512 teal rounded-square badge
```

The source of truth for the artwork is
`custom_components/heating_assistant/icon.svg` (house frame, settling curve,
target). Regenerate PNGs with `python3 scripts/generate-brand-icons.py`
(needs Pillow and cairosvg).

Home Assistant 2026.3+ loads local brand images from
`custom_components/heating_assistant/brand/` (copied into the App package by
`scripts/sync-ha-app-package.sh`). Supervisor uses `heating_assistant/icon.png`
(128×128) and `heating_assistant/logo.png`.
