"""Card library — official cards from the database, merged with built-ins.

The client downloads the `cards` table (public read, anon key) on login,
caches it to disk for offline play, and exposes the merged library. The
Card Designer is the only writer (using the service key, never shipped).
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Callable, Optional
from urllib import error as _urlerr
from urllib import request as _urlreq

from arcanum.core.constants import SUPABASE_ANON_KEY, SUPABASE_URL, user_data_dir
from arcanum.game.cardspec import CardSpec

log = logging.getLogger(__name__)

CACHE_FILE = user_data_dir() / "cards_cache.json"

_lock = threading.Lock()
_official: list[CardSpec] = []


def official_cards() -> list[CardSpec]:
    with _lock:
        return list(_official)


def _set_official(specs: list[CardSpec]) -> None:
    global _official
    with _lock:
        _official = specs


def _parse_rows(rows: list[dict]) -> list[CardSpec]:
    specs: list[CardSpec] = []
    for row in rows:
        try:
            data = row.get("data") or {}
            data.setdefault("id", row.get("id"))
            data.setdefault("name", row.get("name"))
            spec = CardSpec.from_dict(data)
            ok, why = spec.validate()
            if ok:
                specs.append(spec)
            else:
                log.warning("Skipping invalid card %s: %s", spec.id, why)
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("Skipping unreadable card row: %s", exc)
    return specs


def load_cache() -> int:
    """Load the last downloaded library from disk (offline support)."""
    if not CACHE_FILE.exists():
        return 0
    try:
        rows = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        specs = _parse_rows(rows)
        _set_official(specs)
        return len(specs)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Card cache unreadable: %s", exc)
        return 0


def refresh(done: Optional[Callable[[int, str], None]] = None) -> None:
    """Download the official card library in the background.

    `done(count, error)` fires when finished; count is the library size.
    """
    if not (SUPABASE_URL and SUPABASE_ANON_KEY):
        if done:
            done(len(official_cards()), "")
        return

    def work() -> None:
        url = (SUPABASE_URL.rstrip("/")
               + "/rest/v1/cards?select=id,name,data,collectible"
               + "&collectible=eq.true&order=id.asc")
        req = _urlreq.Request(url, headers={"apikey": SUPABASE_ANON_KEY})
        try:
            with _urlreq.urlopen(req, timeout=15) as resp:
                rows = json.loads(resp.read() or b"[]")
        except (_urlerr.URLError, _urlerr.HTTPError, TimeoutError,
                OSError, json.JSONDecodeError) as exc:
            log.warning("Card library refresh failed: %s", exc)
            if done:
                done(len(official_cards()),
                     "Couldn't reach the card library.")
            return
        specs = _parse_rows(rows)
        _set_official(specs)
        try:
            CACHE_FILE.write_text(json.dumps(rows), encoding="utf-8")
        except OSError:
            log.warning("Couldn't write card cache.")
        log.info("Card library: %d official cards loaded.", len(specs))
        if done:
            done(len(specs), "")

    threading.Thread(target=work, daemon=True).start()
