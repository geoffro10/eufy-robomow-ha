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
    DP_AREA,
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
        self._prev_dp118: int = 0
        self._was_returning: bool = False
        self._expecting_map_save: bool = False
        self._false_poll_count: int = 0
        self._in_map_save: bool = False
        # Flicker suppression: DP126 (mowed area) increments during real mowing
        # but never changes during post-session DP1=True flickers.
        self._prev_dp126: int = 0
        self._dp1_true_polls: int = 0
        self._dp126_confirmed: bool = False

    # ── map-save disambiguation ────────────────────────────────────────────────

    def _handle_coordinator_update(self) -> None:
        """Track DP1/DP118 transitions to suppress RETURNING during map saving.

        Confirmed sequences:
          mowing    → DP1=True,  DP118=0            → MOWING
          returning → DP1=True,  DP118=5–99         → RETURNING
          dock      → DP1=False, DP118 resets
          map save  → DP1=True,  DP118 climbs 0→100 → DOCKED
          done      → DP1=False

        In-place map save (stop without docking):
          DP118 drops from ≥5 to 1–4 while DP1 stays True, then climbs back.

        DP2 (pause/resume) is not tracked here; those cycles have no effect
        on map-save detection.
        """
        dps = self.coordinator.data or {}
        dp1   = dps.get(DP_TASK_ACTIVE, False)
        dp118 = dps.get(DP_PROGRESS, 0)
        dp126 = dps.get(DP_AREA, 0)

        if dp1:
            self._false_poll_count = 0

            if not self._prev_dp1:
                # DP1 just went True: new mow session or post-dock map save.
                if self._expecting_map_save:
                    self._in_map_save = True
                    self._expecting_map_save = False
                else:
                    self._in_map_save = False
                self._was_returning = False
                self._dp1_true_polls = 0
                self._dp126_confirmed = False
            else:
                # DP1 stayed True.
                self._dp1_true_polls += 1
                if dp126 != self._prev_dp126:
                    self._dp126_confirmed = True
                if dp118 >= RETURNING_THRESHOLD:
                    self._was_returning = True
                # In-place map save: DP118 drops from return-range to near-zero
                # while DP1 never dips False (stop-without-dock sequence).
                if (self._prev_dp118 >= RETURNING_THRESHOLD
                        and dp118 < RETURNING_THRESHOLD
                        and dp118 > 0
                        and not self._in_map_save):
                    self._in_map_save = True
                    self._was_returning = False

        else:
            self._false_poll_count += 1

            if self._prev_dp1:
                # DP1 just went False: docked after return, map save ended, or cancelled.
                if self._in_map_save:
                    # Map saving finished — next DP1 True is a fresh session.
                    self._expecting_map_save = False
                elif self._was_returning:
                    # Completed a return trip and docked; post-dock map save follows.
                    self._expecting_map_save = True
                # else: cancelled before returning — leave _expecting_map_save alone;
                # the timeout below clears it if no map save materialises.
                self._in_map_save = False
                self._was_returning = False

            # DP1 stayed False past the timeout: session truly ended (e.g. sunset
            # cancel with no map save).  Clear so the next scheduled mow starts fresh.
            if self._false_poll_count > MAP_SAVE_TIMEOUT_POLLS:
                self._expecting_map_save = False

        self._prev_dp1 = dp1
        self._prev_dp118 = dp118
        self._prev_dp126 = dp126
        super()._handle_coordinator_update()

    # ── activity ──────────────────────────────────────────────────────────────

    @property
    def activity(self) -> LawnMowerActivity:
        dps = self.coordinator.data
        dp1   = dps.get(DP_TASK_ACTIVE, False)
        dp2   = dps.get(DP_PAUSED,      False)
        dp118 = dps.get(DP_PROGRESS,    0)

        if not dp1:
            return LawnMowerActivity.DOCKED
        if dp2:
            return LawnMowerActivity.PAUSED
        if self._in_map_save:
            return LawnMowerActivity.DOCKED
        if RETURNING_THRESHOLD <= dp118 < 100:
            return LawnMowerActivity.RETURNING
        # Suppress MOWING during post-session DP1 flickers: DP126 never increments
        # during flickers but does so every 30–60 s during real mowing.
        if self._dp126_confirmed or self._dp1_true_polls < 2:
            return LawnMowerActivity.MOWING
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
