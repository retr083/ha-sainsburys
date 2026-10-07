"""Sensor platform for the Sainsbury's Groceries integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.util import dt as dt_util

from .entity import SainsburysEntity

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import date, datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import SainsburysData, SainsburysDataUpdateCoordinator
    from .data import SainsburysConfigEntry

PARALLEL_UPDATES = 0
CURRENCY_GBP = "GBP"

type SensorValue = date | datetime | float | int | str | None


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse an API timestamp into a timezone-aware datetime."""
    if value is None:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed


def _parse_date(value: str | None) -> date | None:
    """Parse an API date."""
    return dt_util.parse_date(value) if value else None


def _basket_attributes(data: SainsburysData) -> Mapping[str, object]:
    """Return useful basket details as state attributes."""
    return {
        "minimum_spend": data.basket.minimum_spend,
        "slot_price": data.basket.slot_price,
        "items": [item.to_dict() for item in data.basket.items],
    }


@dataclass(frozen=True, kw_only=True)
class SainsburysSensorEntityDescription(SensorEntityDescription):
    """Describe a Sainsbury's sensor."""

    value_fn: Callable[[SainsburysData], SensorValue]
    attributes_fn: Callable[[SainsburysData], Mapping[str, object]] | None = None


SENSOR_DESCRIPTIONS: tuple[SainsburysSensorEntityDescription, ...] = (
    SainsburysSensorEntityDescription(
        key="basket_total",
        translation_key="basket_total",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_GBP,
        suggested_display_precision=2,
        value_fn=lambda data: data.basket.total_price,
    ),
    SainsburysSensorEntityDescription(
        key="basket_subtotal",
        translation_key="basket_subtotal",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_GBP,
        suggested_display_precision=2,
        value_fn=lambda data: data.basket.subtotal_price,
    ),
    SainsburysSensorEntityDescription(
        key="basket_savings",
        translation_key="basket_savings",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_GBP,
        suggested_display_precision=2,
        value_fn=lambda data: data.basket.savings,
    ),
    SainsburysSensorEntityDescription(
        key="nectar_savings",
        translation_key="nectar_savings",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_GBP,
        suggested_display_precision=2,
        value_fn=lambda data: data.basket.nectar_savings,
    ),
    SainsburysSensorEntityDescription(
        key="basket_item_count",
        translation_key="basket_item_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.basket.item_count,
        attributes_fn=_basket_attributes,
    ),
    SainsburysSensorEntityDescription(
        key="latest_order_status",
        translation_key="latest_order_status",
        value_fn=lambda data: (
            data.latest_order.status if data.latest_order is not None else None
        ),
    ),
    SainsburysSensorEntityDescription(
        key="latest_order_total",
        translation_key="latest_order_total",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_GBP,
        suggested_display_precision=2,
        value_fn=lambda data: (
            data.latest_order.total if data.latest_order is not None else None
        ),
    ),
    SainsburysSensorEntityDescription(
        key="slot_start",
        translation_key="slot_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _parse_datetime(
            data.slot_reservation.slot.start_time
            if data.slot_reservation.slot is not None
            else None
        ),
    ),
    SainsburysSensorEntityDescription(
        key="slot_end",
        translation_key="slot_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _parse_datetime(
            data.slot_reservation.slot.end_time
            if data.slot_reservation.slot is not None
            else None
        ),
    ),
    SainsburysSensorEntityDescription(
        key="amend_cutoff",
        translation_key="amend_cutoff",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _parse_datetime(
            data.latest_order_status.cutoff_time
            if data.latest_order_status is not None
            else None
        ),
    ),
    SainsburysSensorEntityDescription(
        key="delivery_pass_expiry",
        translation_key="delivery_pass_expiry",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: _parse_date(data.customer.delivery_pass_expiry_date),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: SainsburysConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Sainsbury's sensors."""
    async_add_entities(
        SainsburysSensor(entry.runtime_data.coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    )


class SainsburysSensor(SainsburysEntity, SensorEntity):
    """Represent Sainsbury's account data as a sensor."""

    entity_description: SainsburysSensorEntityDescription

    def __init__(
        self,
        coordinator: SainsburysDataUpdateCoordinator,
        entity_description: SainsburysSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.data.customer.user_id}_{entity_description.key}"
        )

    @property
    @override
    def native_value(self) -> SensorValue:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    @override
    def extra_state_attributes(self) -> Mapping[str, object] | None:
        """Return optional sensor attributes."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)
