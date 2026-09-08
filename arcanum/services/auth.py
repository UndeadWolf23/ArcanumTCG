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
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

from arcanum.core.constants import LOCAL_USERS_FILE, SUPABASE_URL

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
# Supabase implementation (wired up when the backend goes live)
# ---------------------------------------------------------------------------
class SupabaseAuthService(AuthService):
    """Placeholder mapping of our interface onto Supabase Auth.

    Implementation plan (supabase-py):
        sign_in            -> client.auth.sign_in_with_password(...)
        sign_up            -> client.auth.sign_up(...)
        sign_in_with_token -> client.auth.set_session(refresh_token=...)
        request_password_reset -> client.auth.reset_password_for_email(...)
        sign_out           -> client.auth.sign_out()
    The remember-me token maps to the Supabase refresh token.
    """

    def __init__(self) -> None:
        if not SUPABASE_URL:
            raise RuntimeError(
                "SupabaseAuthService requires SUPABASE_URL/SUPABASE_ANON_KEY "
                "in arcanum/core/constants.py")
        raise NotImplementedError("Supabase backend is not wired up yet.")

    def sign_in(self, identifier: str, password: str, cb: AuthCallback) -> None: ...
    def sign_up(self, username: str, email: str, password: str, cb: AuthCallback) -> None: ...
    def sign_in_with_token(self, token: str, cb: AuthCallback) -> None: ...
    def request_password_reset(self, email: str, cb: AuthCallback) -> None: ...
    def sign_out(self) -> None: ...
