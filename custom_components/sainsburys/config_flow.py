"""Config flow for the Sainsbury's Groceries integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

import voluptuous as vol
from aiohttp import ClientError, ClientSession, CookieJar
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from pysainsburys import GOLAuth, MFARequiredError, Sainsburys
from pysainsburys.exceptions import AuthError, HttpException

from .const import CONF_SESSION, DOMAIN, LOGGER

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .data import SainsburysConfigEntry

AUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.EMAIL,
                autocomplete="email",
            )
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD,
                autocomplete="current-password",
            )
        ),
    }
)

MFA_SCHEMA = vol.Schema(
    {
        vol.Required("code"): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.TEXT,
                autocomplete="one-time-code",
            )
        )
    }
)


class SainsburysConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sainsbury's Groceries."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow state."""
        self._auth: GOLAuth | None = None
        self._email: str | None = None
        self._session: ClientSession | None = None
        self._target_entry: SainsburysConfigEntry | None = None

    @callback
    def _async_release_session(self) -> None:
        """Release the temporary config-flow session."""
        # Sessions from async_create_clientsession share Home Assistant's
        # connector, so they must be detached rather than closed.
        if self._session is not None:
            self._session.detach()
        self._session = None

    @override
    @callback
    def async_remove(self) -> None:
        """Release the session if the flow is abandoned."""
        self._async_release_session()

    async def _async_login(
        self,
        user_input: Mapping[str, Any],
    ) -> tuple[dict[str, str], ConfigFlowResult | None]:
        """Validate credentials and return errors or an MFA form."""
        self._async_release_session()
        self._session = async_create_clientsession(
            self.hass,
            cookie_jar=CookieJar(unsafe=True),
        )
        self._auth = GOLAuth(session=self._session)
        self._email = user_input[CONF_EMAIL]

        try:
            await self._auth.login(
                user_input[CONF_EMAIL],
                user_input[CONF_PASSWORD],
                exchange_commerce=True,
            )
        except MFARequiredError:
            return {}, self.async_show_form(
                step_id="mfa",
                data_schema=MFA_SCHEMA,
                description_placeholders={"email": self._email},
            )
        except AuthError:
            self._async_release_session()
            return {"base": "invalid_auth"}, None
        except ClientError, HttpException, TimeoutError:
            self._async_release_session()
            return {"base": "cannot_connect"}, None
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected exception during Sainsbury's login")
            self._async_release_session()
            return {"base": "unknown"}, None

        return {}, await self._async_create_or_update_entry()

    async def _async_create_or_update_entry(self) -> ConfigFlowResult:
        """Validate account access and create or update the config entry."""
        if self._auth is None or self._email is None:
            msg = "Authentication flow state is incomplete"
            raise RuntimeError(msg)

        client = Sainsburys(self._auth)
        try:
            customer = await client.get_customer()
        except AuthError:
            self._async_release_session()
            return self.async_abort(reason="invalid_auth")
        except ClientError, HttpException, TimeoutError:
            self._async_release_session()
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(customer.user_id)
        data = {
            CONF_EMAIL: self._email,
            CONF_SESSION: self._auth.to_dict(),
        }
        self._async_release_session()

        if self._target_entry is not None:
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                self._target_entry,
                data_updates=data,
            )

        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=customer.display_name, data=data)

    @override
    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial configuration step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, result = await self._async_login(user_input)
            if result is not None:
                return result

        return self.async_show_form(
            step_id="user",
            data_schema=AUTH_SCHEMA,
            errors=errors,
        )

    async def async_step_mfa(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Complete login with a multi-factor authentication code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._auth is None:
                msg = "MFA flow started without authentication state"
                raise RuntimeError(msg)
            try:
                await self._auth.send_mfa_request(
                    user_input["code"],
                    exchange_commerce=True,
                )
            except AuthError:
                errors["base"] = "invalid_mfa"
            except ClientError, HttpException, TimeoutError:
                errors["base"] = "cannot_connect"
            else:
                return await self._async_create_or_update_entry()

        return self.async_show_form(
            step_id="mfa",
            data_schema=MFA_SCHEMA,
            errors=errors,
            description_placeholders={"email": self._email or ""},
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> ConfigFlowResult:
        """Start reauthentication."""
        del entry_data
        self._target_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Prompt for new credentials after authentication failure."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, result = await self._async_login(user_input)
            if result is not None:
                return result

        if self._target_entry is None:
            msg = "Reauthentication flow has no target entry"
            raise RuntimeError(msg)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                AUTH_SCHEMA,
                {CONF_EMAIL: self._target_entry.data.get(CONF_EMAIL)},
            ),
            errors=errors,
            description_placeholders={"name": self._target_entry.title},
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Allow the account credentials to be changed."""
        self._target_entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, result = await self._async_login(user_input)
            if result is not None:
                return result

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                AUTH_SCHEMA,
                {CONF_EMAIL: self._target_entry.data.get(CONF_EMAIL)},
            ),
            errors=errors,
        )
