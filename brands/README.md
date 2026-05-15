# Brand assets

Mirrors the layout of [`home-assistant/brands`](https://github.com/home-assistant/brands)
so these files can be lifted into a PR there unchanged when/if this
integration is ever published.

```
brands/
└── custom_integrations/
    └── heating_assistant/
        ├── icon.png      # 256x256, transparent
        └── icon@2x.png   # 512x512, transparent
```

The source of truth for the artwork is
`custom_components/heating_assistant/icon.svg`; the PNGs here are
rendered from it.
