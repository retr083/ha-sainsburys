"""Tests for Sainsbury's entities and diagnostics."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sainsburys.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    SainsburysBinarySensor,
)
from custom_components.sainsburys.binary_sensor import (
    async_setup_entry as async_setup_binary_sensors,
)
from custom_components.sainsburys.const import DOMAIN
from custom_components.sainsburys.coordinator import (
    SainsburysData,
    SainsburysDataUpdateCoordinator,
)
from custom_components.sainsburys.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.sainsburys.sensor import (
    SENSOR_DESCRIPTIONS,
    SainsburysSensor,
    _parse_date,
    _parse_datetime,
)
from custom_components.sainsburys.sensor import (
    async_setup_entry as async_setup_sensors,
)


def _coordinator(data: SainsburysData) -> SainsburysDataUpdateCoordinator:
    """Build a coordinator mock for entity tests."""
    coordinator = MagicMock(spec=SainsburysDataUpdateCoordinator)
    coordinator.data = data
    coordinator.last_update_success = True
    return coordinator


async def test_sensor_values(sainsburys_data: SainsburysData) -> None:
    """Test all sensor descriptions produce expected states."""
    coordinator = _coordinator(sainsburys_data)
    entities = [
        SainsburysSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    values = {entity.entity_description.key: entity.native_value for entity in entities}

    assert values["basket_total"] == 11.0
    assert values["basket_item_count"] == 1
    assert values["latest_order_status"] == "confirmed"
    assert values["slot_start"].tzinfo is not None
    assert str(values["delivery_pass_expiry"]) == "2027-01-01"
    item_sensor = next(
        entity
        for entity in entities
        if entity.entity_description.key == "basket_item_count"
    )
    assert item_sensor.extra_state_attributes["items"][0]["name"] == "Milk"
    assert entities[0].extra_state_attributes is None


def test_invalid_timestamp_helpers() -> None:
    """Test missing and malformed API dates are ignored."""
    assert _parse_datetime(None) is None
    assert _parse_datetime("not-a-date") is None
    assert _parse_date(None) is None


async def test_binary_sensor_values(sainsburys_data: SainsburysData) -> None:
    """Test all binary sensor descriptions."""
    coordinator = _coordinator(sainsburys_data)
    entities = [
        SainsburysBinarySensor(coordinator, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    ]
    assert all(entity.is_on for entity in entities)


async def test_platform_setup(
    hass: HomeAssistant,
    sainsburys_data: SainsburysData,
) -> None:
    """Test entity platforms add all configured descriptions."""
    coordinator = _coordinator(sainsburys_data)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.runtime_data = MagicMock(coordinator=coordinator)
    add_entities = MagicMock()

    await async_setup_sensors(hass, entry, add_entities)
    sensor_entities = list(add_entities.call_args.args[0])
    assert len(sensor_entities) == len(SENSOR_DESCRIPTIONS)

    await async_setup_binary_sensors(hass, entry, add_entities)
    binary_entities = list(add_entities.call_args.args[0])
    assert len(binary_entities) == len(BINARY_SENSOR_DESCRIPTIONS)


async def test_diagnostics(
    hass: HomeAssistant,
    sainsburys_data: SainsburysData,
) -> None:
    """Test diagnostics contain useful data without account details."""
    entry = MockConfigEntry(domain=DOMAIN, version=1)
    entry.runtime_data = MagicMock()
    entry.runtime_data.coordinator.data = sainsburys_data

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["basket"]["item_count"] == 1
    assert diagnostics["slot_reservation"]["has_slot"] is True
    serialized = str(diagnostics)
    assert "person@example.com" not in serialized
    assert "user-123" not in serialized
    assert "Milk" not in serialized


def test_monetary_sensors_have_valid_state_class() -> None:
    """Test monetary sensors only use state classes HA accepts for them."""
    monetary = [
        description
        for description in SENSOR_DESCRIPTIONS
        if description.device_class is SensorDeviceClass.MONETARY
    ]

    assert {description.key for description in monetary} == {
        "basket_total",
        "basket_subtotal",
        "basket_savings",
        "nectar_savings",
        "latest_order_total",
    }
    assert all(
        description.state_class in (None, SensorStateClass.TOTAL)
        for description in monetary
    )


async def test_entities_without_active_order(
    sainsburys_data: SainsburysData,
) -> None:
    """Test order entities report no data when there is no active order."""
    data = replace(
        sainsburys_data,
        latest_order=None,
        latest_order_status=None,
    )
    coordinator = _coordinator(data)

    values = {
        description.key: SainsburysSensor(coordinator, description).native_value
        for description in SENSOR_DESCRIPTIONS
    }
    assert values["latest_order_status"] is None
    assert values["latest_order_total"] is None
    assert values["amend_cutoff"] is None

    binary_values = {
        description.key: SainsburysBinarySensor(coordinator, description).is_on
        for description in BINARY_SENSOR_DESCRIPTIONS
    }
    assert binary_values["order_amendable"] is False
