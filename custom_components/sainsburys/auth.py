"""Authentication helpers for the Sainsbury's Groceries integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, override

from pysainsburys import GOLAuth

if TYPE_CHECKING:
    from aiohttp import ClientSession


class SainsburysAuth(GOLAuth):
    """Authenticator that reports session changes as soon as they happen."""

    # The OAuth refresh token is single use: every refresh invalidates the
    # previous one. The new tokens therefore have to be saved immediately, not
    # only after a whole coordinator update succeeds, or a later failure leaves
    # the stored token unusable.

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the authenticator."""
        super().__init__(*args, **kwargs)
        self.on_session_update: Callable[[], None] | None = None
        self._refresh_lock = asyncio.Lock()

    def _notify_session_update(self) -> None:
        """Tell the owner that the session data may have changed."""
        if self.on_session_update is not None:
            self.on_session_update()

    @override
    async def send_refresh_request(self) -> None:
        """Refresh tokens, serialised so a single-use token is spent once."""
        async with self._refresh_lock:
            # The library skips the request if a concurrent call already
            # refreshed while this one waited for the lock.
            await super().send_refresh_request()
            self._notify_session_update()

    @override
    async def exchange_commerce_session(
        self,
        *,
        food_profile_create: bool = True,
    ) -> dict[str, Any]:
        """Exchange tokens for a commerce session and report the new one."""
        data = await super().exchange_commerce_session(
            food_profile_create=food_profile_create,
        )
        self._notify_session_update()
        return data


def create_auth(
    data: Mapping[str, Any],
    session: ClientSession,
) -> SainsburysAuth:
    """Create a pysainsburys authenticator using Home Assistant's session."""
    cookies = data.get("cookies")
    auth = SainsburysAuth(
        access_token=data.get("access_token"),
        refresh_token=data.get("refresh_token"),
        wc_auth_token=data.get("wc_auth_token"),
        user_id=data.get("user_id"),
        wc_trusted_token=data.get("wc_trusted_token"),
        cookies=(
            {str(key): str(value) for key, value in cookies.items()}
            if isinstance(cookies, Mapping)
            else None
        ),
        app_version=data.get("app_version"),
        login_hint=data.get("login_hint"),
        session=session,
    )
    auth.personalization_id = data.get("personalization_id")
    if isinstance(next_refresh := data.get("next_refresh"), str):
        auth.next_refresh = datetime.fromisoformat(next_refresh)
    return auth
