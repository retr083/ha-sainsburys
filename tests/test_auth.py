"""Tests for Sainsbury's authentication helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pysainsburys import GOLAuth
from pysainsburys.exceptions import AuthError, UnknownEndpointError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sainsburys.auth import create_auth
from custom_components.sainsburys.const import CONF_SESSION, DOMAIN
from custom_components.sainsburys.coordinator import SainsburysDataUpdateCoordinator


def test_create_auth(session_data: dict[str, object]) -> None:
    """Test stored session data is restored into a library authenticator."""
    session = MagicMock()
    auth = create_auth(session_data, session)

    assert auth.access_token == "access-token"
    assert auth.refresh_token == "refresh-token"
    assert auth.user_id == "user-123"
    assert auth.cookies == {"session": "cookie"}
    assert auth.personalization_id == "personalization"
    assert auth.next_refresh is not None


def test_create_auth_minimal() -> None:
    """Test missing optional session values are supported."""
    auth = create_auth({"cookies": "invalid"}, MagicMock())

    assert auth.access_token is None
    assert auth.cookies == {}
    assert auth.next_refresh is None


async def test_rotated_tokens_persist_when_update_later_fails(
    hass: HomeAssistant,
    session_data: dict[str, object],
) -> None:
    """Test a refresh is saved even if the same update fails afterwards."""
    # Refresh tokens are single use, so losing a rotated token makes the next
    # setup attempt fail with an invalid token.
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user-123",
        data={CONF_EMAIL: "person@example.com", CONF_SESSION: session_data},
    )
    entry.add_to_hass(hass)
    auth = create_auth(session_data, MagicMock())

    async def refresh_then_fail() -> None:
        # Emulates get_customer() refreshing tokens before a later call fails.
        await auth.send_refresh_request()
        raise UnknownEndpointError(500, "failed")

    async def fake_refresh(self: GOLAuth) -> None:
        self.access_token = "rotated-access"
        self._refresh_token = "rotated-refresh"
        self.next_refresh = datetime.now(UTC) + timedelta(hours=1)

    client = MagicMock()
    client.get_customer = AsyncMock(side_effect=refresh_then_fail)
    coordinator = SainsburysDataUpdateCoordinator(hass, entry, client, auth)
    auth.on_session_update = coordinator.async_store_session

    with (
        patch.object(GOLAuth, "send_refresh_request", fake_refresh),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()

    stored = entry.data[CONF_SESSION]
    assert stored["refresh_token"] == "rotated-refresh"
    assert stored["access_token"] == "rotated-access"


async def test_concurrent_refreshes_spend_token_once(
    session_data: dict[str, object],
) -> None:
    """Test overlapping requests do not reuse the same single-use token."""
    auth = create_auth({**session_data, "next_refresh": None}, MagicMock())
    calls = 0

    async def fake_refresh(self: GOLAuth) -> None:
        nonlocal calls
        if self.next_refresh is not None and self.next_refresh > datetime.now(UTC):
            return
        calls += 1
        await asyncio.sleep(0)
        self._refresh_token = f"refresh-{calls}"
        self.next_refresh = datetime.now(UTC) + timedelta(hours=1)

    with patch.object(GOLAuth, "send_refresh_request", fake_refresh):
        await asyncio.gather(*(auth.send_refresh_request() for _ in range(3)))

    assert calls == 1
    assert auth.refresh_token == "refresh-1"


async def test_commerce_exchange_notifies(session_data: dict[str, object]) -> None:
    """Test a new commerce session is reported to the owner."""
    auth = create_auth(session_data, MagicMock())
    auth.on_session_update = notify = MagicMock()

    with patch.object(
        GOLAuth,
        "exchange_commerce_session",
        AsyncMock(return_value={"user_id": "user-123"}),
    ):
        assert await auth.exchange_commerce_session() == {"user_id": "user-123"}

    notify.assert_called_once_with()


async def test_failed_refresh_does_not_notify(
    session_data: dict[str, object],
) -> None:
    """Test nothing is saved when the refresh itself fails."""
    auth = create_auth(session_data, MagicMock())
    auth.on_session_update = notify = MagicMock()

    with (
        patch.object(
            GOLAuth,
            "send_refresh_request",
            AsyncMock(side_effect=AuthError),
        ),
        pytest.raises(AuthError),
    ):
        await auth.send_refresh_request()

    notify.assert_not_called()
