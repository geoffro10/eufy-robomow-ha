"""Lawn mower entity for Eufy Robomow."""
from __future__ import annotations

import logging

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    DP_TASK_ACTIVE,
    DP_PAUSED,
    DP_PROGRESS,
    CMD_START,
    CMD_PAUSE,
    CMD_RESUME,
    CMD_DOCK,
    RETURNING_THRESHOLD,
    MAP_SAVE_TIMEOUT_POLLS,
    CONF_DEVICE_NAME,
)
from .coordinator import EufyMowerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EufyMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EufyRobomowEntity(coordinator, entry)])


class EufyRobomowEntity(CoordinatorEntity[EufyMowerCoordinator], LawnMowerEntity):
    """Represents the Eufy E15 robot mower."""

    _attr_has_entity_name = True
    _attr_translation_key = "lawn_mower"
    _attr_icon = "mdi:robot-mower"
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )

    def __init__(
        self,
        coordinator: EufyMowerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_mower"
        # Note: The cloud device names seem to be internal model IDs.
        # E18 returns "eufy S1200" assuming to be based on the Terramow S1200
        # E15 equivalent is unknown -- falls back to "Eufy Robomow" if not found
        name = entry.data.get(CONF_DEVICE_NAME, "Eufy Robomow")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_DEVICE_ID])},
            name=name,
            manufacturer="Eufy (Anker)",
            model=name,
        )
        # Map-save state tracking (see _handle_coordinator_update docstring)
        self._prev_dp1: bool = False
        self._was_returning: bool = False
        self._expecting_map_save: bool = False
        self._false_poll_count: int = 0
        self._in_map_save: bool = False

    # ── map-save disambiguation ────────────────────────────────────────────────

    def _handle_coordinator_update(self) -> None:
        """Track DP1/DP118 transitions to distinguish map saving from returning.

        End-of-session sequence:
          returning  → DP1=True,  DP118 climbs          (RETURNING)
          dock       → DP1 briefly False, DP118 resets to 0
          map save   → DP1=True,  DP118 climbs again    (should be MOWING)
          done       → DP1=False

        Mid-session charge dock: DP1 stays True throughout — no False transition,
        so _expecting_map_save is never set and a subsequent return shows RETURNING.

        Cancelled session (e.g. sunset): DP1 goes False and stays False.
        After MAP_SAVE_TIMEOUT_POLLS consecutive False polls we abandon the
        map-save expectation so the next scheduled mow starts fresh.
        """
        dps = self.coordinator.data or {}
        dp1   = dps.get(DP_TASK_ACTIVE, False)
        dp118 = dps.get(DP_PROGRESS, 0)

        if dp1:
            self._false_poll_count = 0

            if not self._prev_dp1:
                # DP1 just went True — either a new mow session or map-save start.
                if self._expecting_map_save:
                    self._in_map_save = True
                    self._expecting_map_save = False
                else:
                    self._in_map_save = False
                self._was_returning = False

            if dp118 >= RETURNING_THRESHOLD:
                self._was_returning = True

        else:
            self._false_poll_count += 1

            if self._prev_dp1:
                # DP1 just went False — mower docked after a return trip, OR session
                # was cancelled.  Only arm map-save expectation if we actually saw a
                # return trip AND we aren't already in a map-save phase (which would
                # mean this False is the end of map saving, not the dock-before-save).
                if self._was_returning and not self._in_map_save:
                    self._expecting_map_save = True
                self._in_map_save = False
                self._was_returning = False

            # Safety: if DP1 stays False longer than the timeout the session really
            # ended (e.g. sunset cancel with no map save).  Clear the expectation so
            # the next session starts clean.
            if self._false_poll_count > MAP_SAVE_TIMEOUT_POLLS:
                self._expecting_map_save = False

        self._prev_dp1 = dp1
        super()._handle_coordinator_update()

    # ── activity ──────────────────────────────────────────────────────────────

    @property
    def activity(self) -> LawnMowerActivity:
        dps = self.coordinator.data
        dp1   = dps.get(DP_TASK_ACTIVE, False)
        dp2   = dps.get(DP_PAUSED,      False)
        dp118 = dps.get(DP_PROGRESS,    0)

        # Paused: task active but movement stopped
        if dp1 and dp2:
            return LawnMowerActivity.PAUSED

        if dp1 and not dp2:
            # DP118 5–99 while not in post-dock map-save phase → physically returning.
            # _in_map_save suppresses RETURNING when DP118 climbs after a dock event.
            if RETURNING_THRESHOLD <= dp118 < 100 and not self._in_map_save:
                try:
                    return LawnMowerActivity.RETURNING
                except AttributeError:
                    return LawnMowerActivity.MOWING
            if self._in_map_save:
                return LawnMowerActivity.DOCKED
            return LawnMowerActivity.MOWING

        # DP1 absent or False → no active session → docked / idle
        return LawnMowerActivity.DOCKED

    # ── commands ──────────────────────────────────────────────────────────────

    async def async_start_mowing(self) -> None:
        """Start or resume mowing."""
        current = self.activity
        if current == LawnMowerActivity.PAUSED:
            dp, val = CMD_RESUME
        else:
            dp, val = CMD_START
        await self.coordinator.async_send_command(dp, val)

    async def async_pause(self) -> None:
        """Pause the mowing session."""
        dp, val = CMD_PAUSE
        await self.coordinator.async_send_command(dp, val)

    async def async_dock(self) -> None:
        """Stop mowing and return to base."""
        dp, val = CMD_DOCK
        await self.coordinator.async_send_command(dp, val)
