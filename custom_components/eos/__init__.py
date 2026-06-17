from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import EosDataUpdateCoordinator

PLATFORMS = ["sensor", "climate", "light", "fan", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EOS from a config entry."""
    coordinator = EosDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an EOS config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and DOMAIN in hass.data:
        coordinator: EosDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        if coordinator:
            await coordinator.async_shutdown()
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry after an options update."""
    await hass.config_entries.async_reload(entry.entry_id)
