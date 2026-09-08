"""Authentication service layer.

Scenes only ever talk to the `AuthService` interface. Today that resolves to
`LocalAuthService` (a JSON file on disk, development only). When the backend
goes live, `SupabaseAuthService` is finished and swapped in via
`services.backend.Backend` — no scene code changes.

SECURITY NOTE: the local implementation is a development stand-in. Real
credential handling (hashing policy, rate limiting, resets, email
verification) is delegated to Supabase Auth in production.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
from urllib import error as _urlerr
from urllib import request as _urlreq
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

from arcanum.core.constants import (LOCAL_USERS_FILE, SUPABASE_ANON_KEY,
                                    SUPABASE_URL)

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class User:
    id: str
    username: str
    email: str
    is_guest: bool = False


@dataclass
class AuthResult:
    ok: bool
    user: Optional[User] = None
    error: str = ""
    remember_token: str = ""
    access_token: str = ""      # short-lived JWT for database (PostgREST) calls


AuthCallback = Callable[[AuthResult], None]


class AuthService(ABC):
    """All methods are asynchronous by contract: results arrive via callback.

    Implementations must invoke callbacks from a worker thread; callers are
    expected to marshal back to the main thread (the scenes do this through
    the app's event bus / thread-safe flags).
    """

    @abstractmethod
    def sign_in(self, identifier: str, password: str, cb: AuthCallback) -> None: ...

    @abstractmethod
    def sign_up(self, username: str, email: str, password: str, cb: AuthCallback) -> None: ...

    @abstractmethod
    def sign_in_with_token(self, token: str, cb: AuthCallback) -> None: ...

    @abstractmethod
    def request_password_reset(self, email: str, cb: AuthCallback) -> None: ...

    @abstractmethod
    def sign_out(self) -> None: ...

    # ------------------------------------------------------------------
    @staticmethod
    def guest_user() -> User:
        """Temporary account used by the dev 'Bypass' button."""
        return User(id="guest-dev", username="Playtester", email="", is_guest=True)


def _run_async(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True).start()


# ---------------------------------------------------------------------------
# Local (development) implementation
# ---------------------------------------------------------------------------
class LocalAuthService(AuthService):
    """File-backed fake auth so the whole login flow works offline."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    # -- storage helpers -----------------------------------------------------
    def _load(self) -> dict:
        if LOCAL_USERS_FILE.exists():
            try:
                return json.loads(LOCAL_USERS_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                log.warning("local_users.json unreadable; starting fresh")
        return {"users": {}, "tokens": {}}

    def _save(self, db: dict) -> None:
        LOCAL_USERS_FILE.write_text(json.dumps(db, indent=2), encoding="utf-8")

    @staticmethod
    def _hash(password: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), 200_000).hex()

    # -- interface -----------------------------------------------------------
    def sign_in(self, identifier: str, password: str, cb: AuthCallback) -> None:
        def work() -> None:
            with self._lock:
                db = self._load()
                record = None
                for entry in db["users"].values():
                    if identifier.lower() in (entry["username"].lower(), entry["email"].lower()):
                        record = entry
                        break
                if record is None or self._hash(password, record["salt"]) != record["hash"]:
                    cb(AuthResult(ok=False, error="Incorrect username or password."))
                    return
                token = secrets.token_urlsafe(32)
                db["tokens"][token] = record["id"]
                self._save(db)
                user = User(record["id"], record["username"], record["email"])
                cb(AuthResult(ok=True, user=user, remember_token=token))
        _run_async(work)

    def sign_up(self, username: str, email: str, password: str, cb: AuthCallback) -> None:
        def work() -> None:
            username_clean = username.strip()
            email_clean = email.strip().lower()
            if len(username_clean) < 3:
                cb(AuthResult(ok=False, error="Username must be at least 3 characters."))
                return
            if not EMAIL_RE.match(email_clean):
                cb(AuthResult(ok=False, error="Enter a valid email address."))
                return
            if len(password) < 8:
                cb(AuthResult(ok=False, error="Password must be at least 8 characters."))
                return
            with self._lock:
                db = self._load()
                for entry in db["users"].values():
                    if entry["username"].lower() == username_clean.lower():
                        cb(AuthResult(ok=False, error="That username is taken."))
                        return
                    if entry["email"] == email_clean:
                        cb(AuthResult(ok=False, error="An account with that email already exists."))
                        return
                uid = secrets.token_hex(8)
                salt = secrets.token_hex(16)
                db["users"][uid] = {
                    "id": uid, "username": username_clean, "email": email_clean,
                    "salt": salt, "hash": self._hash(password, salt),
                }
                token = secrets.token_urlsafe(32)
                db["tokens"][token] = uid
                self._save(db)
                cb(AuthResult(ok=True,
                              user=User(uid, username_clean, email_clean),
                              remember_token=token))
        _run_async(work)

    def sign_in_with_token(self, token: str, cb: AuthCallback) -> None:
        def work() -> None:
            with self._lock:
                db = self._load()
                uid = db["tokens"].get(token)
                record = db["users"].get(uid) if uid else None
                if record is None:
                    cb(AuthResult(ok=False, error="Saved session expired."))
                    return
                cb(AuthResult(ok=True,
                              user=User(record["id"], record["username"], record["email"]),
                              remember_token=token))
        _run_async(work)

    def request_password_reset(self, email: str, cb: AuthCallback) -> None:
        def work() -> None:
            if not EMAIL_RE.match(email.strip().lower()):
                cb(AuthResult(ok=False, error="Enter a valid email address."))
                return
            # Local mode has no mail server; pretend it worked.
            cb(AuthResult(ok=True))
        _run_async(work)

    def sign_out(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Supabase implementation (GoTrue Auth REST API — stdlib only, no SDK)
# ---------------------------------------------------------------------------
class SupabaseAuthService(AuthService):
    """Real accounts backed by Supabase Auth.

    Endpoints used (all under {project}/auth/v1):
      signup                          -> create account (username in metadata)
      token?grant_type=password       -> email + password sign-in
      token?grant_type=refresh_token  -> remember-me / silent sign-in
      recover                         -> password-reset email

    Notes:
    * Supabase signs in by EMAIL. Usernames are stored as display names in
      user metadata; username login needs a profiles table (later milestone).
    * Refresh tokens ROTATE: every silent sign-in returns a new token, which
      the session store persists automatically.
    """

    def __init__(self, url: str = SUPABASE_URL, key: str = SUPABASE_ANON_KEY) -> None:
        if not url or not key:
            raise RuntimeError(
                "Supabase requires SUPABASE_URL and SUPABASE_ANON_KEY "
                "(env: ARCANUM_SUPABASE_URL / ARCANUM_SUPABASE_KEY).")
        self.base = url.rstrip("/") + "/auth/v1"
        self.key = key

    # -- http ----------------------------------------------------------------
    def _post(self, path: str, body: dict, query: str = "") -> tuple[int, dict]:
        req = _urlreq.Request(
            self.base + path + query,
            data=json.dumps(body).encode("utf-8"),
            headers={
                # apikey alone is correct for auth endpoints and works with
                # both legacy anon JWTs and new sb_publishable_... keys
                "apikey": self.key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with _urlreq.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except _urlerr.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"{}")
            except (json.JSONDecodeError, OSError):
                return exc.code, {}
        except (_urlerr.URLError, TimeoutError, OSError) as exc:
            log.warning("Supabase request failed: %s", exc)
            return 0, {"error_description": "network"}

    @staticmethod
    def _friendly(status: int, data: dict) -> str:
        raw = str(data.get("error_description") or data.get("msg")
                  or data.get("message") or data.get("error") or "").lower()
        if status == 0 or "network" in raw:
            return "Can't reach the account server — check your connection."
        if "invalid login credentials" in raw:
            return "Incorrect email or password."
        if "already registered" in raw or "already been registered" in raw:
            return "An account with that email already exists."
        if "email not confirmed" in raw:
            return "Confirm your email first — check your inbox."
        if "rate limit" in raw or status == 429:
            return "Too many attempts — wait a minute and try again."
        if "password" in raw and ("short" in raw or "at least" in raw):
            return "Password must be at least 8 characters."
        return "Something went wrong — please try again."

    @staticmethod
    def _user_from(data: dict) -> Optional[User]:
        info = data.get("user") or {}
        uid = info.get("id")
        if not uid:
            return None
        email = info.get("email", "")
        meta = info.get("user_metadata") or {}
        username = meta.get("username") or (email.split("@")[0] if email else "Player")
        return User(id=str(uid), username=str(username), email=str(email))

    # -- interface -----------------------------------------------------------
    def sign_in(self, identifier: str, password: str, cb: AuthCallback) -> None:
        def work() -> None:
            email = identifier.strip().lower()
            if "@" not in email:
                cb(AuthResult(ok=False,
                              error="Sign in with the email on your account."))
                return
            status, data = self._post("/token", {"email": email,
                                                 "password": password},
                                      query="?grant_type=password")
            if status == 200 and data.get("access_token"):
                user = self._user_from(data)
                if user is None:
                    cb(AuthResult(ok=False, error="Unexpected server reply."))
                    return
                cb(AuthResult(ok=True, user=user,
                              remember_token=data.get("refresh_token", ""),
                              access_token=data.get("access_token", "")))
            else:
                cb(AuthResult(ok=False, error=self._friendly(status, data)))
        _run_async(work)

    def sign_up(self, username: str, email: str, password: str,
                cb: AuthCallback) -> None:
        def work() -> None:
            username_clean = username.strip()
            email_clean = email.strip().lower()
            if len(username_clean) < 3:
                cb(AuthResult(ok=False, error="Username must be at least 3 characters."))
                return
            if not EMAIL_RE.match(email_clean):
                cb(AuthResult(ok=False, error="Enter a valid email address."))
                return
            if len(password) < 8:
                cb(AuthResult(ok=False, error="Password must be at least 8 characters."))
                return
            status, data = self._post("/signup", {
                "email": email_clean, "password": password,
                "data": {"username": username_clean,
                         "display_name": username_clean,
                         "full_name": username_clean},
            })
            if status == 200 and data.get("access_token"):
                # email confirmation is OFF: we're signed in immediately
                user = self._user_from(data)
                cb(AuthResult(ok=True, user=user,
                              remember_token=data.get("refresh_token", ""),
                              access_token=data.get("access_token", "")))
            elif status == 200:
                # confirmation is ON: account made, must verify before login
                cb(AuthResult(ok=False, error=(
                    "Account created! Confirm it via the email we sent, "
                    "then sign in.")))
            else:
                cb(AuthResult(ok=False, error=self._friendly(status, data)))
        _run_async(work)

    def sign_in_with_token(self, token: str, cb: AuthCallback) -> None:
        def work() -> None:
            status, data = self._post("/token", {"refresh_token": token},
                                      query="?grant_type=refresh_token")
            if status == 200 and data.get("access_token"):
                user = self._user_from(data)
                if user is None:
                    cb(AuthResult(ok=False, error="Saved session expired."))
                    return
                # tokens rotate — hand back the NEW one to persist
                cb(AuthResult(ok=True, user=user,
                              remember_token=data.get("refresh_token", token),
                              access_token=data.get("access_token", "")))
            else:
                cb(AuthResult(ok=False, error="Saved session expired."))
        _run_async(work)

    def request_password_reset(self, email: str, cb: AuthCallback) -> None:
        def work() -> None:
            email_clean = email.strip().lower()
            if not EMAIL_RE.match(email_clean):
                cb(AuthResult(ok=False, error="Enter a valid email address."))
                return
            status, data = self._post("/recover", {"email": email_clean})
            if status == 200:
                cb(AuthResult(ok=True))
            else:
                cb(AuthResult(ok=False, error=self._friendly(status, data)))
        _run_async(work)

    def sign_out(self) -> None:
        pass  # local session clearing is handled by Session.end()
