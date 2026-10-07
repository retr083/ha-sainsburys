"""Tests for the Sainsbury's config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientConnectionError
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pysainsburys import Customer, MFARequiredError
from pysainsburys.exceptions import AuthError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sainsburys.config_flow import SainsburysConfigFlow
from custom_components.sainsburys.const import CONF_SESSION, DOMAIN

USER_INPUT = {
    CONF_EMAIL: "person@example.com",
    CONF_PASSWORD: "secret",
}


async def test_user_form(hass: HomeAssistant) -> None:
    """Test the initial form is displayed."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


def _mock_login(session_data: dict[str, object]) -> tuple[MagicMock, MagicMock]:
    """Build mocked pysainsburys auth and client objects."""
    auth = MagicMock()
    auth.login = AsyncMock()
    auth.send_mfa_request = AsyncMock()
    auth.to_dict.return_value = session_data
    client = MagicMock()
    client.get_customer = AsyncMock(
        return_value=Customer(
            user_id="user-123",
            email="person@example.com",
            given_name="Test",
            family_name="Shopper",
        )
    )
    return auth, client


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_success(
    hass: HomeAssistant,
    session_data: dict[str, object],
) -> None:
    """Test a successful user flow."""
    auth, client = _mock_login(session_data)
    session = MagicMock(closed=False)

    with (
        patch(
            "custom_components.sainsburys.config_flow.async_create_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.sainsburys.config_flow.GOLAuth",
            return_value=auth,
        ),
        patch(
            "custom_components.sainsburys.config_flow.Sainsburys",
            return_value=client,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=USER_INPUT,
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Shopper"
    assert result["data"] == {
        CONF_EMAIL: "person@example.com",
        CONF_SESSION: session_data,
    }
    assert result["result"].unique_id == "user-123"
    session.detach.assert_called_once()
    session.close.assert_not_called()


async def test_mfa_flow(
    hass: HomeAssistant,
    session_data: dict[str, object],
) -> None:
    """Test a login that requires MFA."""
    auth, client = _mock_login(session_data)
    auth.login.side_effect = MFARequiredError
    session = MagicMock(closed=False)

    with (
        patch(
            "custom_components.sainsburys.config_flow.async_create_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.sainsburys.config_flow.GOLAuth",
            return_value=auth,
        ),
        patch(
            "custom_components.sainsburys.config_flow.Sainsburys",
            return_value=client,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=USER_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "mfa"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"code": "123456"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    auth.send_mfa_request.assert_awaited_once_with(
        "123456",
        exchange_commerce=True,
    )


@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (AuthError(), "invalid_mfa"),
        (ClientConnectionError(), "cannot_connect"),
    ],
)
async def test_mfa_errors(
    hass: HomeAssistant,
    session_data: dict[str, object],
    exception: Exception,
    error: str,
) -> None:
    """Test MFA validation errors."""
    auth, client = _mock_login(session_data)
    auth.login.side_effect = MFARequiredError
    auth.send_mfa_request.side_effect = exception
    session = MagicMock(closed=False)

    with (
        patch(
            "custom_components.sainsburys.config_flow.async_create_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.sainsburys.config_flow.GOLAuth",
            return_value=auth,
        ),
        patch(
            "custom_components.sainsburys.config_flow.Sainsburys",
            return_value=client,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=USER_INPUT,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"code": "bad-code"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (AuthError(), "invalid_auth"),
        (ClientConnectionError(), "cannot_connect"),
        (RuntimeError(), "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    session_data: dict[str, object],
    exception: Exception,
    error: str,
) -> None:
    """Test errors while signing in."""
    auth, client = _mock_login(session_data)
    auth.login.side_effect = exception
    session = MagicMock(closed=False)

    with (
        patch(
            "custom_components.sainsburys.config_flow.async_create_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.sainsburys.config_flow.GOLAuth",
            return_value=auth,
        ),
        patch(
            "custom_components.sainsburys.config_flow.Sainsburys",
            return_value=client,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=USER_INPUT,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_duplicate_account(
    hass: HomeAssistant,
    session_data: dict[str, object],
) -> None:
    """Test that an account cannot be configured twice."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id="user-123", data={})
    entry.add_to_hass(hass)
    auth, client = _mock_login(session_data)
    session = MagicMock(closed=False)

    with (
        patch(
            "custom_components.sainsburys.config_flow.async_create_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.sainsburys.config_flow.GOLAuth",
            return_value=auth,
        ),
        patch(
            "custom_components.sainsburys.config_flow.Sainsburys",
            return_value=client,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=USER_INPUT,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("exception", "reason"),
    [
        (AuthError(), "invalid_auth"),
        (ClientConnectionError(), "cannot_connect"),
    ],
)
async def test_customer_validation_errors(
    hass: HomeAssistant,
    session_data: dict[str, object],
    exception: Exception,
    reason: str,
) -> None:
    """Test errors validating the account after login."""
    auth, client = _mock_login(session_data)
    client.get_customer.side_effect = exception
    session = MagicMock(closed=False)

    with (
        patch(
            "custom_components.sainsburys.config_flow.async_create_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.sainsburys.config_flow.GOLAuth",
            return_value=auth,
        ),
        patch(
            "custom_components.sainsburys.config_flow.Sainsburys",
            return_value=client,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=USER_INPUT,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


@pytest.mark.parametrize(
    "source",
    [config_entries.SOURCE_REAUTH, config_entries.SOURCE_RECONFIGURE],
)
async def test_update_credentials(
    hass: HomeAssistant,
    session_data: dict[str, object],
    source: str,
) -> None:
    """Test reauthentication and reconfiguration."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user-123",
        data={
            CONF_EMAIL: "old@example.com",
            CONF_SESSION: {"access_token": "old"},
        },
    )
    entry.add_to_hass(hass)
    auth, client = _mock_login(session_data)
    session = MagicMock(closed=False)

    with (
        patch(
            "custom_components.sainsburys.config_flow.async_create_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.sainsburys.config_flow.GOLAuth",
            return_value=auth,
        ),
        patch(
            "custom_components.sainsburys.config_flow.Sainsburys",
            return_value=client,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": source, "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            USER_INPUT,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] in {"reauth_successful", "reconfigure_successful"}
    assert entry.data[CONF_EMAIL] == "person@example.com"
    assert entry.data[CONF_SESSION] == session_data


async def test_abandoned_flow_detaches_session(hass: HomeAssistant) -> None:
    """Test removing a flow detaches its session without closing it."""
    session = MagicMock(closed=False)
    flow = SainsburysConfigFlow()
    flow.hass = hass
    flow._session = session

    flow.async_remove()

    session.detach.assert_called_once()
    session.close.assert_not_called()
    assert flow._session is None
