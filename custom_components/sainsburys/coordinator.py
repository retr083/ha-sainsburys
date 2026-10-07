"""Data coordinator for the Sainsbury's Groceries integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from aiohttp import ClientError
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pysainsburys.exceptions import AuthError, HttpException

from .const import CONF_SESSION, DEFAULT_SCAN_INTERVAL, DOMAIN, LOGGER

HTTP_NOT_FOUND = 404
ORDER_NOT_FOUND_CODE = "ORDER_NOT_FOUND"

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pysainsburys import (
        Basket,
        Customer,
        GOLAuth,
        OrderStatus,
        OrderSummary,
        Sainsburys,
        SlotReservation,
    )

    from .data import SainsburysConfigEntry


def _is_order_not_found(err: HttpException) -> bool:
    """Return whether the API reported that the account has no active order."""
    return err.status == HTTP_NOT_FOUND and any(
        error.get("code") == ORDER_NOT_FOUND_CODE for error in err.errors
    )


@dataclass(frozen=True, slots=True)
class SainsburysData:
    """Data fetched for one Sainsbury's account."""

    customer: Customer
    basket: Basket
    latest_order: OrderSummary | None
    latest_order_status: OrderStatus | None
    slot_reservation: SlotReservation


class SainsburysDataUpdateCoordinator(DataUpdateCoordinator[SainsburysData]):
    """Coordinate updates from Sainsbury's Groceries."""

    config_entry: SainsburysConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SainsburysConfigEntry,
        client: Sainsburys,
        auth: GOLAuth,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self.auth = auth

    @override
    async def _async_update_data(self) -> SainsburysData:
        """Fetch account, basket, order, and reserved-slot data."""
        try:
            customer = await self.client.get_customer()
            basket = await customer.basket.fetch()
            orders = await customer.orders.fetch()
            latest_order = orders.orders[0] if orders.orders else None
            latest_status = (
                await self._async_fetch_order_status(customer) if latest_order else None
            )
            reservation = await customer.slots.fetch_reservation()
        except AuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="authentication_failed",
            ) from err
        except (ClientError, HttpException, TimeoutError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
            ) from err

        self.async_store_session()
        return SainsburysData(
            customer=customer,
            basket=basket,
            latest_order=latest_order,
            latest_order_status=latest_status,
            slot_reservation=reservation,
        )

    async def _async_fetch_order_status(self, customer: Customer) -> OrderStatus | None:
        """Fetch live order status, or None when there is no active order."""
        try:
            return await customer.orders.latest.status()
        except HttpException as err:
            if not _is_order_not_found(err):
                raise
            return None

    @callback
    def async_store_session(self) -> None:
        """Persist refreshed authentication state in the config entry."""
        session_data = self.auth.to_dict()
        if session_data == self.config_entry.data.get(CONF_SESSION):
            return
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_SESSION: session_data},
        )
