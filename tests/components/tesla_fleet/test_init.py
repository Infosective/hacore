"""Test the Tesla Fleet init."""

from freezegun.api import FrozenDateTimeFactory
import pytest
from syrupy import SnapshotAssertion
from tesla_fleet_api.exceptions import (
    InvalidToken,
    LoginRequired,
    OAuthExpired,
    RateLimited,
    TeslaFleetError,
    VehicleOffline,
)

from homeassistant.components.tesla_fleet.coordinator import (
    ENERGY_INTERVAL,
    ENERGY_INTERVAL_SECONDS,
    VEHICLE_INTERVAL,
    VEHICLE_INTERVAL_SECONDS,
    VEHICLE_WAIT,
)
from homeassistant.components.tesla_fleet.models import TeslaFleetData
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from . import setup_platform
from .const import VEHICLE_ASLEEP, VEHICLE_DATA_ALT

from tests.common import MockConfigEntry, async_fire_time_changed

ERRORS = [
    (InvalidToken, ConfigEntryState.SETUP_ERROR),
    (OAuthExpired, ConfigEntryState.SETUP_ERROR),
    (LoginRequired, ConfigEntryState.SETUP_ERROR),
    (TeslaFleetError, ConfigEntryState.SETUP_RETRY),
]


async def test_load_unload(
    hass: HomeAssistant,
    normal_config_entry: MockConfigEntry,
) -> None:
    """Test load and unload."""

    await setup_platform(hass, normal_config_entry)

    assert normal_config_entry.state is ConfigEntryState.LOADED
    assert isinstance(normal_config_entry.runtime_data, TeslaFleetData)
    assert await hass.config_entries.async_unload(normal_config_entry.entry_id)
    await hass.async_block_till_done()
    assert normal_config_entry.state is ConfigEntryState.NOT_LOADED
    assert not hasattr(normal_config_entry, "runtime_data")


@pytest.mark.parametrize(("side_effect", "state"), ERRORS)
async def test_init_error(
    hass: HomeAssistant,
    normal_config_entry: MockConfigEntry,
    mock_products,
    side_effect,
    state,
) -> None:
    """Test init with errors."""

    mock_products.side_effect = side_effect
    await setup_platform(hass, normal_config_entry)
    assert normal_config_entry.state is state


# Test devices
async def test_devices(
    hass: HomeAssistant,
    normal_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test device registry."""
    await setup_platform(hass, normal_config_entry)
    devices = dr.async_entries_for_config_entry(
        device_registry, normal_config_entry.entry_id
    )

    for device in devices:
        assert device == snapshot(name=f"{device.identifiers}")


# Vehicle Coordinator
async def test_vehicle_refresh_offline(
    hass: HomeAssistant,
    normal_config_entry: MockConfigEntry,
    mock_vehicle_state,
    mock_vehicle_data,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test coordinator refresh with an error."""
    await setup_platform(hass, normal_config_entry)
    assert normal_config_entry.state is ConfigEntryState.LOADED

    mock_vehicle_state.assert_called_once()
    mock_vehicle_data.assert_called_once()
    mock_vehicle_state.reset_mock()
    mock_vehicle_data.reset_mock()

    # Test the unlikely condition that a vehicle state is online but actually offline
    mock_vehicle_data.side_effect = VehicleOffline
    freezer.tick(VEHICLE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    mock_vehicle_state.assert_called_once()
    mock_vehicle_data.assert_called_once()
    mock_vehicle_state.reset_mock()
    mock_vehicle_data.reset_mock()

    # Test the normal condition that a vehcile state is offline
    mock_vehicle_state.return_value = VEHICLE_ASLEEP
    freezer.tick(VEHICLE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    mock_vehicle_state.assert_called_once()
    mock_vehicle_data.assert_not_called()


@pytest.mark.parametrize(("side_effect"), ERRORS)
async def test_vehicle_refresh_error(
    hass: HomeAssistant,
    normal_config_entry: MockConfigEntry,
    mock_vehicle_state,
    side_effect,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test coordinator refresh makes entity unavailable."""

    await setup_platform(hass, normal_config_entry)

    mock_vehicle_state.side_effect = side_effect
    freezer.tick(VEHICLE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert (state := hass.states.get("sensor.test_battery_level"))
    assert state.state == "unavailable"


async def test_vehicle_refresh_ratelimited(
    hass: HomeAssistant,
    normal_config_entry: MockConfigEntry,
    mock_vehicle_data,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test coordinator refresh handles 429."""

    mock_vehicle_data.side_effect = RateLimited(
        {"after": VEHICLE_INTERVAL_SECONDS + 10}
    )
    await setup_platform(hass, normal_config_entry)

    assert (state := hass.states.get("sensor.test_battery_level"))
    assert state.state == "unknown"
    assert mock_vehicle_data.call_count == 1

    freezer.tick(VEHICLE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # Should not call for another 10 seconds
    assert mock_vehicle_data.call_count == 1

    freezer.tick(VEHICLE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert mock_vehicle_data.call_count == 2


async def test_vehicle_sleep(
    hass: HomeAssistant,
    normal_config_entry: MockConfigEntry,
    mock_vehicle_data,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test coordinator refresh with an error."""
    await setup_platform(hass, normal_config_entry)
    assert mock_vehicle_data.call_count == 1

    freezer.tick(VEHICLE_WAIT + VEHICLE_INTERVAL)
    async_fire_time_changed(hass)
    # Let vehicle sleep, no updates for 15 minutes
    await hass.async_block_till_done()
    assert mock_vehicle_data.call_count == 2

    freezer.tick(VEHICLE_INTERVAL)
    async_fire_time_changed(hass)
    # No polling, call_count should not increase
    await hass.async_block_till_done()
    assert mock_vehicle_data.call_count == 2

    freezer.tick(VEHICLE_INTERVAL)
    async_fire_time_changed(hass)
    # No polling, call_count should not increase
    await hass.async_block_till_done()
    assert mock_vehicle_data.call_count == 2

    freezer.tick(VEHICLE_WAIT)
    async_fire_time_changed(hass)
    # Vehicle didn't sleep, go back to normal
    await hass.async_block_till_done()
    assert mock_vehicle_data.call_count == 3

    freezer.tick(VEHICLE_INTERVAL)
    async_fire_time_changed(hass)
    # Regular polling
    await hass.async_block_till_done()
    assert mock_vehicle_data.call_count == 4

    mock_vehicle_data.return_value = VEHICLE_DATA_ALT
    freezer.tick(VEHICLE_INTERVAL)
    async_fire_time_changed(hass)
    # Vehicle active
    await hass.async_block_till_done()
    assert mock_vehicle_data.call_count == 5

    freezer.tick(VEHICLE_WAIT)
    async_fire_time_changed(hass)
    # Dont let sleep when active
    await hass.async_block_till_done()
    assert mock_vehicle_data.call_count == 6

    freezer.tick(VEHICLE_WAIT)
    async_fire_time_changed(hass)
    # Dont let sleep when active
    await hass.async_block_till_done()
    assert mock_vehicle_data.call_count == 7


# Test Energy Live Coordinator
@pytest.mark.parametrize(("side_effect", "state"), ERRORS)
async def test_energy_live_refresh_error(
    hass: HomeAssistant,
    normal_config_entry: MockConfigEntry,
    mock_live_status,
    side_effect,
    state,
) -> None:
    """Test coordinator refresh with an error."""
    mock_live_status.side_effect = side_effect
    await setup_platform(hass, normal_config_entry)
    assert normal_config_entry.state is state


# Test Energy Site Coordinator
@pytest.mark.parametrize(("side_effect", "state"), ERRORS)
async def test_energy_site_refresh_error(
    hass: HomeAssistant,
    normal_config_entry: MockConfigEntry,
    mock_site_info,
    side_effect,
    state,
) -> None:
    """Test coordinator refresh with an error."""
    mock_site_info.side_effect = side_effect
    await setup_platform(hass, normal_config_entry)
    assert normal_config_entry.state is state


async def test_energy_live_refresh_ratelimited(
    hass: HomeAssistant,
    normal_config_entry: MockConfigEntry,
    mock_live_status,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test coordinator refresh handles 429."""

    await setup_platform(hass, normal_config_entry)

    mock_live_status.side_effect = RateLimited({"after": ENERGY_INTERVAL_SECONDS + 10})
    freezer.tick(ENERGY_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert mock_live_status.call_count == 2

    freezer.tick(ENERGY_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # Should not call for another 10 seconds
    assert mock_live_status.call_count == 2

    freezer.tick(ENERGY_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert mock_live_status.call_count == 3


async def test_energy_info_refresh_ratelimited(
    hass: HomeAssistant,
    normal_config_entry: MockConfigEntry,
    mock_site_info,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test coordinator refresh handles 429."""

    await setup_platform(hass, normal_config_entry)

    mock_site_info.side_effect = RateLimited({"after": ENERGY_INTERVAL_SECONDS + 10})
    freezer.tick(ENERGY_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert mock_site_info.call_count == 2

    freezer.tick(ENERGY_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # Should not call for another 10 seconds
    assert mock_site_info.call_count == 2

    freezer.tick(ENERGY_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert mock_site_info.call_count == 3
