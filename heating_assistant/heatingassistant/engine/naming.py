"""Shared naming helpers for entity IDs and slugs."""

from __future__ import annotations

import re
import unicodedata


def slugify(value: str) -> str:
    """Slugify a string the same way Home Assistant does for entity IDs.

    Lowercases, applies NFKD Unicode normalisation, strips non-ASCII bytes,
    removes punctuation (e.g. apostrophes), and collapses runs of
    whitespace/hyphens to underscores.  This matches the slug HA derives from
    ``_attr_name`` for the integration's entities: "Erik's Room" → "eriks_room"
    rather than "erik_s_room".
    """
    text = unicodedata.normalize("NFKD", value.lower())
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "_", text).strip("_")
    return text or "_"


def room_slug(value: str) -> str:
    """App / Ingress room slug used for synthetic entity IDs and forecast keys.

    Unlike :func:`slugify`, punctuation becomes underscores rather than being
    stripped — ``Erik's Room`` → ``erik_s_room``. Keep this in sync with
    Ingress discovery and ``HeatingRuntime`` entity naming (SWD-277).
    """
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return re.sub(r"_+", "_", slug)
