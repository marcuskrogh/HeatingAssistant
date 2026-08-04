# Vendored Ipopt / cyipopt wheels

Binary wheels that provide `import cyipopt` with a bundled Ipopt library so
non-linear MPC works on Home Assistant hosts without a system Ipopt install.

| Source | Platforms |
|--------|-----------|
| [cyipopt-wheels](https://pypi.org/project/cyipopt-wheels/) on PyPI | manylinux x86_64 / aarch64, macOS arm64 (cp312, cp313) |
| Built via `scripts/build_cyipopt_musllinux.sh` (+ CI workflow) | musllinux (HAOS) x86_64 / aarch64 (cp312, cp313) |

On setup the integration installs `cyipopt-wheels` from PyPI when possible, then
falls back to the matching wheel in this directory.

Licenses of bundled native libraries (Ipopt, MUMPS, OpenBLAS, …) are those of
the upstream wheel / COIN-OR distributions (typically EPL-2.0 for Ipopt).
