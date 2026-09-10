"""Profiles + friendships over Supabase REST.

Everything runs on worker threads and reports back through callbacks, same
pattern as the deck store — call from the UI thread, receive a *Result later,
apply it inside update(). The friendships table's RLS does the policing;
this module just speaks the protocol politely.

Vocabulary:
  profile   {id, username, coins}
  relation  {id, user_id, username, kind}   kind: friend | incoming | outgoing
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error as _urlerr
import urllib.parse as _urlparse
import urllib.request as _urlreq
from dataclasses import dataclass, field
from typing import Callable, Optional

from arcanum.core.constants import SUPABASE_ANON_KEY, SUPABASE_URL

log = logging.getLogger(__name__)

USERNAME_MIN, USERNAME_MAX = 3, 16


def username_problem(name: str) -> str:
    """'' when the name is acceptable, else a human reason."""
    name = name.strip()
    if len(name) < USERNAME_MIN:
        return f"At least {USERNAME_MIN} characters."
    if len(name) > USERNAME_MAX:
        return f"At most {USERNAME_MAX} characters."
    if not all(ch.isalnum() or ch in "_-" for ch in name):
        return "Letters, numbers, _ and - only."
    if not name[0].isalpha():
        return "Must start with a letter."
    return ""


@dataclass
class SocialResult:
    ok: bool
    error: str = ""
    profile: Optional[dict] = None
    users: list = field(default_factory=list)       # search hits
    relations: list = field(default_factory=list)   # friends + requests


class SupabaseSocial:
    def __init__(self, access_token: str, user_id: str,
                 url: str = SUPABASE_URL, key: str = SUPABASE_ANON_KEY) -> None:
        if not (url and key and access_token and user_id):
            raise RuntimeError("Social service needs URL, key, and a "
                               "signed-in session.")
        self.base = url.rstrip("/") + "/rest/v1"
        self.key = key
        self.token = access_token
        self.user_id = user_id

    # ------------------------------------------------------------- plumbing
    def _request(self, method: str, path: str, query: str = "",
                 body=None, prefer: str = "") -> tuple[int, list | dict]:
        headers = {"apikey": self.key,
                   "Authorization": f"Bearer {self.token}",
                   "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode() if body is not None else None
        req = _urlreq.Request(self.base + path + query, data=data,
                              headers=headers, method=method)
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
            log.warning("Social request failed: %s", exc)
            return 0, {}

    def _spawn(self, fn: Callable, callback: Callable) -> None:
        def run() -> None:
            try:
                callback(fn())
            except Exception:  # noqa: BLE001
                log.exception("Social operation crashed")
                callback(SocialResult(ok=False,
                                      error="Something went wrong."))
        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _friendly(status: int) -> str:
        if status == 0:
            return "Can't reach the server — check your connection."
        if status in (401, 403):
            return "Your session expired — please sign in again."
        return f"Server said no (HTTP {status})."

    # ------------------------------------------------------------- profile
    def ensure_profile(self, fallback_name: str,
                       callback: Callable[[SocialResult], None]) -> None:
        """Fetch my profile; create it for pre-trigger accounts."""
        def work() -> SocialResult:
            status, rows = self._request(
                "GET", "/profiles", f"?id=eq.{self.user_id}&select=*")
            if status == 200 and rows:
                return SocialResult(ok=True, profile=rows[0])
            if status != 200:
                return SocialResult(ok=False, error=self._friendly(status))
            # no row yet (account predates the signup trigger) — create one
            name = fallback_name if not username_problem(fallback_name) \
                else f"Mage-{self.user_id[:6]}"
            status, rows = self._request(
                "POST", "/profiles", "",
                body={"id": self.user_id, "username": name},
                prefer="return=representation")
            if status == 409:                     # name taken: salt it
                salted = f"{name[:USERNAME_MAX - 5]}-{self.user_id[:4]}"
                status, rows = self._request(
                    "POST", "/profiles", "",
                    body={"id": self.user_id, "username": salted},
                    prefer="return=representation")
            if status in (200, 201) and rows:
                return SocialResult(ok=True, profile=rows[0])
            return SocialResult(ok=False, error=self._friendly(status))
        self._spawn(work, callback)

    def set_username(self, name: str,
                     callback: Callable[[SocialResult], None]) -> None:
        problem = username_problem(name)
        if problem:
            callback(SocialResult(ok=False, error=problem))
            return

        def work() -> SocialResult:
            # pre-check availability for a friendly message; the unique
            # index remains the true referee (409 on races)
            quoted = _urlparse.quote(name.strip())
            status, rows = self._request(
                "GET", "/profiles",
                f"?username=eq.{quoted}&select=id")
            if status == 200 and rows and rows[0]["id"] != self.user_id:
                return SocialResult(ok=False,
                                    error="That name is already taken.")
            status, rows = self._request(
                "PATCH", "/profiles", f"?id=eq.{self.user_id}",
                body={"username": name.strip()},
                prefer="return=representation")
            if status == 409:
                return SocialResult(ok=False,
                                    error="That name is already taken.")
            if status in (200, 204) and rows:
                return SocialResult(ok=True, profile=rows[0])
            return SocialResult(ok=False, error=self._friendly(status))
        self._spawn(work, callback)

    # -------------------------------------------------------------- friends
    def search_users(self, text: str,
                     callback: Callable[[SocialResult], None]) -> None:
        def work() -> SocialResult:
            cleaned = text.strip().replace("-", "").upper()
            if len(cleaned) == 8 and cleaned.isalnum():
                # looks like a friend code: exact match first
                status, rows = self._request(
                    "GET", "/profiles",
                    f"?friend_code=eq.{cleaned}&id=neq.{self.user_id}"
                    "&select=id,username")
                if status == 200 and rows:
                    return SocialResult(ok=True, users=list(rows))
            quoted = _urlparse.quote(f"*{text.strip()}*")
            status, rows = self._request(
                "GET", "/profiles",
                f"?username=ilike.{quoted}&id=neq.{self.user_id}"
                f"&select=id,username&limit=10&order=username")
            if status != 200:
                return SocialResult(ok=False, error=self._friendly(status))
            return SocialResult(ok=True, users=list(rows))
        self._spawn(work, callback)

    def list_relations(self, callback: Callable[[SocialResult], None]) -> None:
        """Friends + pending requests, each row resolved to a username."""
        def work() -> SocialResult:
            status, rows = self._request(
                "GET", "/friendships",
                f"?or=(from_id.eq.{self.user_id},to_id.eq.{self.user_id})"
                "&select=id,from_id,to_id,status")
            if status != 200:
                return SocialResult(ok=False, error=self._friendly(status))
            other_ids = sorted({(r["to_id"] if r["from_id"] == self.user_id
                                 else r["from_id"]) for r in rows})
            names: dict[str, str] = {}
            if other_ids:
                id_list = ",".join(f'"{i}"' for i in other_ids)
                status, people = self._request(
                    "GET", "/profiles",
                    f"?id=in.({id_list})&select=id,username")
                if status == 200:
                    names = {p["id"]: p["username"] for p in people}
            relations = []
            for row in rows:
                outgoing = row["from_id"] == self.user_id
                other = row["to_id"] if outgoing else row["from_id"]
                kind = ("friend" if row["status"] == "accepted"
                        else "outgoing" if outgoing else "incoming")
                relations.append({"id": row["id"], "user_id": other,
                                  "username": names.get(other,
                                                        other[:8] + "…"),
                                  "kind": kind})
            order = {"incoming": 0, "friend": 1, "outgoing": 2}
            relations.sort(key=lambda r: (order[r["kind"]],
                                          r["username"].lower()))
            return SocialResult(ok=True, relations=relations)
        self._spawn(work, callback)

    def send_request(self, to_id: str,
                     callback: Callable[[SocialResult], None]) -> None:
        def work() -> SocialResult:
            status, _ = self._request(
                "POST", "/friendships", "",
                body={"from_id": self.user_id, "to_id": to_id,
                      "status": "pending"})
            if status == 409:
                return SocialResult(ok=False,
                                    error="A request already exists "
                                          "between you two.")
            if status in (200, 201):
                return SocialResult(ok=True)
            return SocialResult(ok=False, error=self._friendly(status))
        self._spawn(work, callback)

    def accept_request(self, relation_id: str,
                       callback: Callable[[SocialResult], None]) -> None:
        def work() -> SocialResult:
            status, _ = self._request(
                "PATCH", "/friendships", f"?id=eq.{relation_id}",
                body={"status": "accepted"})
            if status in (200, 204):
                return SocialResult(ok=True)
            return SocialResult(ok=False, error=self._friendly(status))
        self._spawn(work, callback)

    def remove_relation(self, relation_id: str,
                        callback: Callable[[SocialResult], None]) -> None:
        """Decline, cancel, or unfriend — all just delete the row."""
        def work() -> SocialResult:
            status, _ = self._request(
                "DELETE", "/friendships", f"?id=eq.{relation_id}")
            if status in (200, 204):
                return SocialResult(ok=True)
            return SocialResult(ok=False, error=self._friendly(status))
        self._spawn(work, callback)
