"""Card image cache — downloads published card images once, keeps them local.

Images live in the app's data directory (data/card_images/). The deck builder
asks for a card's image every frame; this module answers instantly from the
in-memory/disk cache and quietly downloads missing ones in the background
from the public `card-art` storage bucket. Failures are remembered for the
session so we don't hammer the network.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from urllib import error as _urlerr
from urllib import request as _urlreq

from arcanum.core.constants import SUPABASE_URL, user_data_dir

log = logging.getLogger(__name__)

CACHE_DIR = user_data_dir() / "card_images"

_lock = threading.Lock()
_in_flight: set[str] = set()
_failed: set[str] = set()


def _local_path(image_name: str) -> Path:
    safe = image_name.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / safe


def public_url(image_name: str) -> str:
    return (SUPABASE_URL.rstrip("/")
            + "/storage/v1/object/public/card-art/" + image_name)


def get_path(image_name: str) -> Path | None:
    """Local file if cached; otherwise starts a background download and
    returns None (call again later — the UI polls each frame anyway)."""
    if not image_name:
        return None
    path = _local_path(image_name)
    if path.is_file() and path.stat().st_size > 0:
        return path
    if not SUPABASE_URL:
        return None
    with _lock:
        if image_name in _failed or image_name in _in_flight:
            return None
        _in_flight.add(image_name)
    threading.Thread(target=_download, args=(image_name,), daemon=True).start()
    return None


def _download(image_name: str) -> None:
    url = public_url(image_name)
    try:
        req = _urlreq.Request(url)
        with _urlreq.urlopen(req, timeout=20) as resp:
            data = resp.read()
        if not data:
            raise OSError("empty response")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _local_path(image_name).with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(_local_path(image_name))
        log.info("Cached card image %s (%d KB)", image_name, len(data) // 1024)
    except (_urlerr.URLError, _urlerr.HTTPError, TimeoutError, OSError) as exc:
        log.info("Card image %s unavailable: %s", image_name, exc)
        with _lock:
            _failed.add(image_name)
    finally:
        with _lock:
            _in_flight.discard(image_name)
