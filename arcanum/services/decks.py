"""Deck persistence.

SupabaseDeckStore talks to the `decks` table through PostgREST using the
player's ACCESS TOKEN, so Row Level Security guarantees players only ever
touch their own rows. LocalDeckStore (a JSON file) covers offline play and
the dev Bypass account. Both are asynchronous: results arrive via callback
from a worker thread, same pattern as auth.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib import error as _urlerr
from urllib import parse as _urlparse
from urllib import request as _urlreq

from arcanum.core.constants import SUPABASE_ANON_KEY, SUPABASE_URL, user_data_dir

log = logging.getLogger(__name__)

LOCAL_DECKS_FILE = user_data_dir() / "local_decks.json"


@dataclass
class DeckRecord:
    id: str
    name: str
    cards: dict[str, int] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return sum(self.cards.values())


@dataclass
class DeckResult:
    ok: bool
    decks: list[DeckRecord] = field(default_factory=list)
    deck: Optional[DeckRecord] = None
    error: str = ""


DeckCallback = Callable[[DeckResult], None]


def _run_async(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True).start()


class DeckStore(ABC):
    @abstractmethod
    def list_decks(self, cb: DeckCallback) -> None: ...

    @abstractmethod
    def save_deck(self, deck: DeckRecord, cb: DeckCallback) -> None:
        """Insert (empty id) or update (existing id)."""

    @abstractmethod
    def delete_deck(self, deck_id: str, cb: DeckCallback) -> None: ...


# ---------------------------------------------------------------------------
# Local JSON store (offline / guest)
# ---------------------------------------------------------------------------
class LocalDeckStore(DeckStore):
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if LOCAL_DECKS_FILE.exists():
            try:
                return json.loads(LOCAL_DECKS_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                log.warning("local_decks.json unreadable; starting fresh")
        return {"decks": []}

    def _save(self, data: dict) -> None:
        LOCAL_DECKS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_decks(self, cb: DeckCallback) -> None:
        def work() -> None:
            with self._lock:
                data = self._load()
            decks = [DeckRecord(d["id"], d["name"], dict(d["cards"]))
                     for d in data["decks"]]
            cb(DeckResult(ok=True, decks=decks))
        _run_async(work)

    def save_deck(self, deck: DeckRecord, cb: DeckCallback) -> None:
        def work() -> None:
            with self._lock:
                data = self._load()
                if not deck.id:
                    deck.id = uuid.uuid4().hex
                rows = [d for d in data["decks"] if d["id"] != deck.id]
                rows.append({"id": deck.id, "name": deck.name,
                             "cards": deck.cards})
                data["decks"] = rows
                self._save(data)
            cb(DeckResult(ok=True, deck=deck))
        _run_async(work)

    def delete_deck(self, deck_id: str, cb: DeckCallback) -> None:
        def work() -> None:
            with self._lock:
                data = self._load()
                data["decks"] = [d for d in data["decks"] if d["id"] != deck_id]
                self._save(data)
            cb(DeckResult(ok=True))
        _run_async(work)


# ---------------------------------------------------------------------------
# Supabase store (PostgREST + RLS)
# ---------------------------------------------------------------------------
class SupabaseDeckStore(DeckStore):
    """Requires the `decks` table + RLS policies (SQL provided in setup)."""

    def __init__(self, access_token: str,
                 url: str = SUPABASE_URL, key: str = SUPABASE_ANON_KEY) -> None:
        if not (url and key and access_token):
            raise RuntimeError("Supabase deck store needs URL, key, and a "
                               "signed-in session.")
        self.base = url.rstrip("/") + "/rest/v1/decks"
        self.key = key
        self.token = access_token

    def _request(self, method: str, query: str = "", body: dict | list | None = None,
                 prefer: str = "") -> tuple[int, list | dict]:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode() if body is not None else None
        req = _urlreq.Request(self.base + query, data=data, headers=headers,
                              method=method)
        try:
            with _urlreq.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except _urlerr.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"{}")
            except (json.JSONDecodeError, OSError):
                return exc.code, {}
        except (_urlerr.URLError, TimeoutError, OSError) as exc:
            log.warning("Deck request failed: %s", exc)
            return 0, {}

    @staticmethod
    def _friendly(status: int) -> str:
        if status == 0:
            return "Can't reach the server — check your connection."
        if status == 401:
            return "Session expired — please sign in again."
        if status == 404:
            return "Deck storage isn't set up yet (missing 'decks' table)."
        return "Deck storage error — please try again."

    def list_decks(self, cb: DeckCallback) -> None:
        def work() -> None:
            status, data = self._request(
                "GET", "?select=id,name,cards&order=updated_at.desc")
            if status == 200 and isinstance(data, list):
                decks = [DeckRecord(str(d["id"]), str(d["name"]),
                                    dict(d.get("cards") or {}))
                         for d in data]
                cb(DeckResult(ok=True, decks=decks))
            else:
                cb(DeckResult(ok=False, error=self._friendly(status)))
        _run_async(work)

    def save_deck(self, deck: DeckRecord, cb: DeckCallback) -> None:
        def work() -> None:
            body = {"name": deck.name, "cards": deck.cards}
            if deck.id:
                query = "?id=eq." + _urlparse.quote(deck.id)
                status, data = self._request("PATCH", query, body,
                                             prefer="return=representation")
            else:
                status, data = self._request("POST", "", body,
                                             prefer="return=representation")
            if status in (200, 201) and isinstance(data, list) and data:
                row = data[0]
                deck.id = str(row.get("id", deck.id))
                cb(DeckResult(ok=True, deck=deck))
            else:
                cb(DeckResult(ok=False, error=self._friendly(status)))
        _run_async(work)

    def delete_deck(self, deck_id: str, cb: DeckCallback) -> None:
        def work() -> None:
            query = "?id=eq." + _urlparse.quote(deck_id)
            status, _ = self._request("DELETE", query)
            if status in (200, 204):
                cb(DeckResult(ok=True))
            else:
                cb(DeckResult(ok=False, error=self._friendly(status)))
        _run_async(work)
