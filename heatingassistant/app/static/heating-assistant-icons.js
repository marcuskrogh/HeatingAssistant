/**
 * heating-assistant-icons.js
 *
 * Registers the Heating Assistant logo mark as a custom HA icon set so it can
 * be used anywhere HA accepts an icon string, including the sidebar, via:
 *
 *   heating-assistant:logo
 *
 * The icon is a filled compound path (nonzero winding rule) built from four
 * sub-paths that together recreate the teal logo mark in monochrome:
 *
 *   1. Outer house pentagon (CW)  — provides winding +1 inside
 *   2. Inner house pentagon (CCW) — subtracts winding, leaving a ring frame
 *   3. S-curve band (CW)          — inside the ring cutout, fills the curve
 *   4. Target dot (CW arcs)       — filled circle at the curve's settling point
 *
 * The fill colour is `currentColor` so the icon inherits the sidebar's
 * text/icon colour and highlights correctly when the panel is active.
 */

(function () {
  /**
   * Combined path for the logo in a 0 0 100 100 viewBox.
   *
   * Sub-path 1 — outer house (CW, +1):
   *   M 20 84 L 20 47 L 50 19 L 80 47 L 80 84 Z
   *
   * Sub-path 2 — inner house (CCW, −1, creates ring with sub-path 1):
   *   M 26 84 L 74 84 L 74 50 L 50 25 L 26 50 Z
   *
   * Sub-path 3 — S-curve band (CW, fills inside the ring cutout):
   *   M 31 68 C 43 68 46 51 58 50 C 63 50 67 50 71 50
   *   L 71 55 C 67 55 63 55 58 55 C 46 56 43 76 31 76 Z
   *
   * Sub-path 4 — target dot, CW circle at (71, 52) r=4 via two half-arcs:
   *   M 71 48 A 4 4 0 1 1 71 56 A 4 4 0 1 1 71 48 Z
   */
  const LOGO_PATH =
    'M 20 84 L 20 47 L 50 19 L 80 47 L 80 84 Z ' +
    'M 26 84 L 74 84 L 74 50 L 50 25 L 26 50 Z ' +
    'M 31 68 C 43 68 46 51 58 50 C 63 50 67 50 71 50 ' +
    'L 71 55 C 67 55 63 55 58 55 C 46 56 43 76 31 76 Z ' +
    'M 71 48 A 4 4 0 1 1 71 56 A 4 4 0 1 1 71 48 Z';

  window.customIcons = window.customIcons || {};
  window.customIcons['heating-assistant'] = {
    getIcon(name) {
      if (name === 'logo') {
        return { path: LOGO_PATH, viewBox: '0 0 100 100' };
      }
      return undefined;
    },
  };
})();
