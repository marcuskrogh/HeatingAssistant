/**
 * Panel cache-bust token for versioned ES module static imports (`?v=` suffix).
 *
 * Keep in sync with ``js_url`` in ``__init__.py`` (enforced by test). When
 * bumping, update this constant and re-run the import suffix pass across ``www/js``.
 */
export const PANEL_CACHE_BUST = '97';
