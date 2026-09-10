"""In-process publish/subscribe event bus.

Systems talk to each other through named events instead of direct references,
which keeps scenes, services, and (later) netcode decoupled. Example:

    bus.subscribe(Events.AUTH_LOGIN_SUCCESS, self._on_login)
    bus.publish(Events.AUTH_LOGIN_SUCCESS, user=user)

Handlers run synchronously on the main thread. Network threads should queue
events via `publish_threadsafe`, which the app drains once per frame.
"""
from __future__ import annotations

import logging
import queue
import threading
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger(__name__)

Handler = Callable[..., None]


class Events:
    """Central registry of event names (avoids typo'd string literals)."""
    PROFILE_READY = "profile.ready"

    # App lifecycle
    APP_QUIT = "app.quit"
    DISPLAY_CHANGED = "app.display_changed"     # resolution / mode applied
    SETTINGS_SAVED = "app.settings_saved"

    # Auth / session
    AUTH_LOGIN_SUCCESS = "auth.login_success"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_LOGOUT = "auth.logout"

    # Networking (future server-hosted matches)
    NET_CONNECTED = "net.connected"
    NET_DISCONNECTED = "net.disconnected"
    NET_MESSAGE = "net.message"
    MATCH_FOUND = "net.match_found"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)
        self._pending: "queue.Queue[tuple[str, dict[str, Any]]]" = queue.Queue()
        self._lock = threading.Lock()

    def subscribe(self, event: str, handler: Handler) -> None:
        with self._lock:
            if handler not in self._subscribers[event]:
                self._subscribers[event].append(handler)

    def unsubscribe(self, event: str, handler: Handler) -> None:
        with self._lock:
            if handler in self._subscribers[event]:
                self._subscribers[event].remove(handler)

    def publish(self, event: str, **payload: Any) -> None:
        with self._lock:
            handlers = list(self._subscribers.get(event, ()))
        for handler in handlers:
            try:
                handler(**payload)
            except Exception:  # noqa: BLE001 - one bad handler must not kill the loop
                log.exception("Error in handler for event %r", event)

    def publish_threadsafe(self, event: str, **payload: Any) -> None:
        """Safe to call from worker/network threads; delivered next frame."""
        self._pending.put((event, payload))

    def pump(self) -> None:
        """Drain thread-safe queue. Called once per frame by the app."""
        while True:
            try:
                event, payload = self._pending.get_nowait()
            except queue.Empty:
                return
            self.publish(event, **payload)
