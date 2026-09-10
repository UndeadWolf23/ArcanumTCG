"""Server-side economy: the only writer of gold, packs, and progress.

Talks to Supabase with the SERVICE ROLE key (env SUPABASE_SERVICE_KEY on the
host — never in the repo, never in clients). Every mutation is a Postgres
SECURITY DEFINER function, so prices, discounts, daily caps, and the
welcome-gift flag are enforced in one place even if this process is wrong.

All calls run in a thread executor so the websocket loop never blocks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import urllib.error as _urlerr
import urllib.request as _urlreq
from typing import Any, Optional

from arcanum.core.constants import SUPABASE_URL
from arcanum.game import packs

log = logging.getLogger(__name__)

SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

PACK_IDS = ("adventure", "wonder", "cosmic")
PACK_PRICES = {"adventure": 500, "wonder": 800, "cosmic": 1500}
BUNDLE_DISCOUNT = {1: 0, 3: 5, 10: 10}
CHALLENGE_NAMES = {
    "summon_heroes": "Summon 15 heroes",
    "sacrifice_permanents": "Sacrifice 5 permanents",
    "cast_spells": "Cast 15 spells",
    "gain_life": "Gain 15 life",
    "attack_heroes": "Attack with heroes 20 times",
    "destroy_heroes": "Destroy 10 enemy heroes",
    "draw_cards": "Draw 20 cards",
}


def enabled() -> bool:
    return bool(SUPABASE_URL and SERVICE_KEY)


def _rpc_sync(fn: str, args: dict) -> Optional[Any]:
    if not enabled():
        return None
    url = SUPABASE_URL.rstrip("/") + f"/rest/v1/rpc/{fn}"
    req = _urlreq.Request(
        url, data=json.dumps(args).encode(),
        headers={"apikey": SERVICE_KEY,
                 "Authorization": f"Bearer {SERVICE_KEY}",
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with _urlreq.urlopen(req, timeout=12) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else True
    except _urlerr.HTTPError as exc:
        body = exc.read()[:200].decode(errors="replace")
        log.warning("Economy rpc %s failed (HTTP %s): %s", fn, exc.code, body)
        return None
    except (_urlerr.URLError, TimeoutError, OSError) as exc:
        log.warning("Economy rpc %s network failure: %s", fn, exc)
        return None


async def _rpc(fn: str, args: dict) -> Optional[Any]:
    return await asyncio.get_running_loop().run_in_executor(
        None, _rpc_sync, fn, args)


# ------------------------------------------------------------------ queries
async def wallet(uid: str) -> Optional[dict]:
    data = await _rpc("get_wallet", {"p_uid": uid})
    if not isinstance(data, dict):
        return None
    for entry in data.get("dailies", []):
        entry["name"] = CHALLENGE_NAMES.get(entry.get("id", ""),
                                            entry.get("id", "?"))
    return data


async def claim_welcome(uid: str) -> bool:
    """True exactly once per account (the DB flag is the referee)."""
    result = await _rpc("claim_welcome_gift", {"p_uid": uid})
    return result is True


async def buy(uid: str, pack: str, qty: int) -> dict:
    if pack not in PACK_IDS or qty not in BUNDLE_DISCOUNT:
        return {"ok": False, "error": "bad request"}
    result = await _rpc("buy_packs", {"p_uid": uid, "p_pack": pack,
                                      "p_qty": qty})
    return result if isinstance(result, dict) else \
        {"ok": False, "error": "economy offline"}


async def open_pack(uid: str, pack: str,
                    rng: random.Random | None = None) -> Optional[list[str]]:
    """Consume one pack; roll its cards; bank them in the collection."""
    if pack not in PACK_IDS:
        return None
    taken = await _rpc("consume_pack", {"p_uid": uid, "p_pack": pack})
    if taken is not True:
        return None
    rolled = packs.open_pack(pack, rng or random.Random())
    card_ids = [c.card_id for c in rolled]
    counts: dict[str, int] = {}
    for cid in card_ids:
        counts[cid] = counts.get(cid, 0) + 1
    await _rpc("grant_cards", {"p_uid": uid, "p_cards": counts})
    return card_ids


async def claim_challenge(uid: str, slot: int) -> dict:
    result = await _rpc("claim_challenge", {"p_uid": uid, "p_slot": int(slot)})
    return result if isinstance(result, dict) else \
        {"ok": False, "error": "economy offline"}


async def record_win(uid: str) -> dict:
    result = await _rpc("record_win", {"p_uid": uid})
    return result if isinstance(result, dict) else {"ok": False, "awarded": 0}


async def add_progress(uid: str, counts: dict[str, int]) -> None:
    clean = {k: int(v) for k, v in counts.items()
             if k in CHALLENGE_NAMES and int(v) > 0}
    if clean:
        await _rpc("add_match_progress", {"p_uid": uid, "p_counts": clean})
