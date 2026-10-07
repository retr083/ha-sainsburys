"""Tests for Sainsbury's integration setup and coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import llm
from homeassistant.helpers.update_coordinator import UpdateFailed
from pysainsburys.exceptions import AuthError, UnknownEndpointError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sainsburys import async_setup_entry, async_unload_entry
from custom_components.sainsburys.const import CONF_SESSION, DOMAIN
from custom_components.sainsburys.coordinator import SainsburysDataUpdateCoordinator
from custom_components.sainsburys.llm_api import llm_api_id


async def test_setup_and_unload(
    hass: HomeAssistant,
    session_data: dict[str, object],
) -> None:
    """Test config entry setup and unloading."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user-123",
        data={CONF_EMAIL: "person@example.com", CONF_SESSION: session_data},
    )
    entry.add_to_hass(hass)
    session = MagicMock(closed=False)
    auth = MagicMock()
    client = MagicMock()

    with (
        patch(
            "custom_components.sainsburys.async_create_clientsession",
            return_value=session,
        ),
        patch("custom_components.sainsburys.create_auth", return_value=auth),
        patch("custom_components.sainsburys.Sainsburys", return_value=client),
        patch.object(
            SainsburysDataUpdateCoordinator,
            "async_config_entry_first_refresh",
            AsyncMock(),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(),
        ) as forward,
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=True),
        ) as unload,
    ):
        assert await async_setup_entry(hass, entry)
        assert entry.runtime_data.client is client
        coordinator = entry.runtime_data.coordinator
        assert auth.on_session_update == coordinator.async_store_session
        assert entry.runtime_data.session is session
        assert any(
            api.id == llm_api_id(entry.entry_id) for api in llm.async_get_apis(hass)
        )
        forward.assert_awaited_once()
        assert await async_unload_entry(hass, entry)

    unload.assert_awaited_once()
    session.close.assert_not_called()


async def test_setup_failure_does_not_close_session(
    hass: HomeAssistant,
    session_data: dict[str, object],
) -> None:
    """Test failed setup leaves session cleanup to Home Assistant."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "person@example.com", CONF_SESSION: session_data},
    )
    session = MagicMock(closed=False)

    with (
        patch(
            "custom_components.sainsburys.async_create_clientsession",
            return_value=session,
        ),
        patch("custom_components.sainsburys.create_auth", return_value=MagicMock()),
        patch("custom_components.sainsburys.Sainsburys", return_value=MagicMock()),
        patch.object(
            SainsburysDataUpdateCoordinator,
            "async_config_entry_first_refresh",
            AsyncMock(side_effect=RuntimeError),
        ),
        pytest.raises(RuntimeError),
    ):
        await async_setup_entry(hass, entry)

    session.close.assert_not_called()


async def test_unload_failure_returns_false(
    hass: HomeAssistant,
    session_data: dict[str, object],
) -> None:
    """Test a failed platform unload is reported."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "person@example.com", CONF_SESSION: session_data},
    )
    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        AsyncMock(return_value=False),
    ):
        assert not await async_unload_entry(hass, entry)


async def test_coordinator_update(
    hass: HomeAssistant,
    session_data: dict[str, object],
    sainsburys_data,
) -> None:
    """Test coordinator data collection and refreshed session persistence."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user-123",
        data={CONF_EMAIL: "person@example.com", CONF_SESSION: session_data},
    )
    entry.add_to_hass(hass)
    customer = MagicMock()
    customer.basket.fetch = AsyncMock(return_value=sainsburys_data.basket)
    customer.orders.fetch = AsyncMock(
        return_value=MagicMock(orders=[sainsburys_data.latest_order])
    )
    customer.orders.latest.status = AsyncMock(
        return_value=sainsburys_data.latest_order_status
    )
    customer.slots.fetch_reservation = AsyncMock(
        return_value=sainsburys_data.slot_reservation
    )
    client = MagicMock()
    client.get_customer = AsyncMock(return_value=customer)
    auth = MagicMock()
    refreshed = {**session_data, "access_token": "new"}
    auth.to_dict.return_value = refreshed

    coordinator = SainsburysDataUpdateCoordinator(hass, entry, client, auth)
    result = await coordinator._async_update_data()

    assert result.basket is sainsburys_data.basket
    assert result.latest_order is sainsburys_data.latest_order
    assert entry.data[CONF_SESSION] == refreshed


async def test_unchanged_session_is_not_written(
    hass: HomeAssistant,
    session_data: dict[str, object],
) -> None:
    """Test unchanged token data does not rewrite the config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SESSION: session_data},
    )
    entry.add_to_hass(hass)
    auth = MagicMock()
    auth.to_dict.return_value = session_data
    coordinator = SainsburysDataUpdateCoordinator(
        hass,
        entry,
        MagicMock(),
        auth,
    )

    with patch.object(hass.config_entries, "async_update_entry") as update_entry:
        coordinator.async_store_session()

    update_entry.assert_not_called()


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (AuthError(), ConfigEntryAuthFailed),
        (UnknownEndpointError(500, "failed"), UpdateFailed),
    ],
)
async def test_coordinator_errors(
    hass: HomeAssistant,
    session_data: dict[str, object],
    exception: Exception,
    expected: type[Exception],
) -> None:
    """Test coordinator exception mapping."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SESSION: session_data},
    )
    client = MagicMock()
    client.get_customer = AsyncMock(side_effect=exception)
    coordinator = SainsburysDataUpdateCoordinator(
        hass,
        entry,
        client,
        MagicMock(),
    )
    with pytest.raises(expected):
        await coordinator._async_update_data()
