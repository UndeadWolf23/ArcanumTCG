"""Player session: who is logged in, and the persisted remember-me token.

The token saved here is an opaque string handed back by the auth service
(local token today; Supabase refresh token later). Never store passwords.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from arcanum.core.constants import SESSION_FILE
from arcanum.services.auth import User

log = logging.getLogger(__name__)


class Session:
    def __init__(self) -> None:
        self.user: Optional[User] = None

    # -- state ---------------------------------------------------------------
    @property
    def logged_in(self) -> bool:
        return self.user is not None

    def begin(self, user: User, remember_token: str = "", remember: bool = False) -> None:
        self.user = user
        if remember and remember_token and not user.is_guest:
            self._write_token(remember_token)
        elif not remember:
            self.clear_saved_token()

    def end(self) -> None:
        self.user = None
        self.clear_saved_token()

    # -- remember-me token ------------------------------------------------
    def saved_token(self) -> str:
        if not SESSION_FILE.exists():
            return ""
        try:
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            return str(data.get("remember_token", ""))
        except (json.JSONDecodeError, OSError):
            return ""

    def _write_token(self, token: str) -> None:
        try:
            SESSION_FILE.write_text(
                json.dumps({"remember_token": token}), encoding="utf-8")
        except OSError as exc:
            log.error("Could not persist session: %s", exc)

    def clear_saved_token(self) -> None:
        try:
            SESSION_FILE.unlink(missing_ok=True)
        except OSError:
            pass
