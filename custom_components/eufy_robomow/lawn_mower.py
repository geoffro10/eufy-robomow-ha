"""Lawn mower entity for Eufy Robomow.

State machine driven by DP1 EDGES interpreted by context, not DP1 levels.
Confirmed protocol behavior (live monitoring, stopwatch verified):

- DP1 True->False after a confirmed session  = physical return journey begins
  (N30/N76 fires at this moment; journey takes 1-2 minutes)
- DP1 False->True while returning            = mower physically docked;
  DP118 then climbs 0->100 = MAP SAVING (mower already docked, stay DOCKED)
- DP1 True->False while map saving           = map save complete (stay DOCKED)
- DP1 False->True from idle with no N-code, no DP126 increment
                                             = scheduler flicker (stay DOCKED)
- DP126 increments only during real mowing   = strongest mowing confirmation
- N43 = scheduled start, N41 = resumed after charge (both mean MOWING)
- HA-initiated commands generate NO N-code; we track our own CMD_START instead
"""
from __future__ import annotations

import logging
import time

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    DP_TASK_ACTIVE,
    DP_PAUSED,
    CMD_START,
    CMD_PAUSE,
    CMD_RESUME,
    CMD_DOCK,
)
from .coordinator import EufyMowerCoordinator

_LOGGER = logging.getLogger(__name__)

# DPs referenced directly (documented in const.py / FINDINGS):
DP_NCODE = "114"  # N-code notification register (NOT a live-view state)
DP_AREA = "126"   # mowed-area counter; increments ONLY during real mowing

# N-codes that mark state transitions (confirmed via live monitoring):
RETURN_NCODES = {30, 76}  # N30 low battery return, N76 returning to dock
ACTIVE_NCODES = {41, 43}  # N41 resumed after charge, N43 scheduled start

RETURNING_TIMEOUT = 300        # s - failsafe if dock arrival never observed
MAP_SAVE_TIMEOUT = 240         # s - map saves complete well under 2 minutes
OPTIMISTIC_START_WINDOW = 120  # s - trust our own CMD_START for this long

# Internal phases:
#   idle      - no session; DOCKED
#   candidate - DP1 rose but session not yet confirmed; DOCKED (suppresses
#               scheduler flickers; promoted to mowing by DP126/N-code)
#   mowing    - confirmed active session; MOWING
#   returning - physical return journey (DP1 dropped after confirmed session,
#               or N30/N76 fired); RETURNING
#   map_save  - docked, DP118 climbing; DOCKED


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EufyMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EufyRobomowEntity(coordinator, entry)])


class EufyRobomowEntity(CoordinatorEntity[EufyMowerCoordinator], LawnMowerEntity):
    """Represents the Eufy Robomow mower."""

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
        # Dynamic device name saved by config_flow (PR #10). Falls back
        # gracefully if the entry predates the fix.
        name = entry.data.get("device_name", "Eufy Robomow")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_DEVICE_ID])},
            name=name,
            manufacturer="Eufy (Anker)",
            model=name,
        )

        # --- state machine -------------------------------------------------
        self._phase: str = "idle"
        self._phase_ts: float = time.monotonic()
        self._prev_dp1: bool | None = None
        self._prev_ncode: int | None = None
        self._prev_area: int | None = None
        self._own_start_ts: float | None = None

    # ── helpers ──────────────────────────────────────────────────────────────

    def _dps(self) -> dict:
        return (
            getattr(self.coordinator, "_last_local_dps", None)
            or self.coordinator.data
            or {}
        )

    def _set_phase(self, phase: str, now: float) -> None:
        if phase != self._phase:
            _LOGGER.debug("Mower phase: %s -> %s", self._phase, phase)
            self._phase = phase
            self._phase_ts = now

    # ── state machine (runs ONCE per coordinator refresh) ───────────────────

    def _handle_coordinator_update(self) -> None:
        dps = self._dps()
        dp1 = bool(dps.get(DP_TASK_ACTIVE, False))
        ncode = dps.get(DP_NCODE)
        area = dps.get(DP_AREA)
        now = time.monotonic()

        first_update = self._prev_dp1 is None

        ncode_changed = (
            not first_update
            and self._prev_ncode is not None
            and ncode != self._prev_ncode
        )
        area_incremented = (
            not first_update
            and self._prev_area is not None
            and area is not None
            and area > self._prev_area
        )

        # 1) N-code driven transitions. These work even when DP1 does not
        #    change (mid-session charge cycles can keep DP1 True throughout).
        if ncode_changed and ncode in RETURN_NCODES:
            self._set_phase("returning", now)
        elif ncode_changed and ncode in ACTIVE_NCODES:
            self._set_phase("mowing", now)

        # 2) DP1 rising edge (False -> True)
        if not first_update and dp1 and not self._prev_dp1:
            if self._phase == "returning":
                # Physical arrival at the dock. DP118 will now climb 0->100:
                # that is map saving, the mower is ALREADY docked.
                self._set_phase("map_save", now)
            elif self._phase != "mowing":
                if (
                    self._own_start_ts is not None
                    and now - self._own_start_ts < OPTIMISTIC_START_WINDOW
                ):
                    # We sent CMD_START ourselves (no N-code fires for
                    # HA-initiated commands).
                    self._set_phase("mowing", now)
                elif ncode in ACTIVE_NCODES and ncode != self._prev_ncode:
                    # N43/N41 arrived in the same poll as the rising edge.
                    # Deliberately does NOT require a known previous value:
                    # DP114 can be absent from the local DPS until a session
                    # starts (observed 2026-07-03: None -> 43 at a scheduled
                    # start caused a 4-minute MOWING lag under the stricter
                    # guard). A stale active code after a restart cannot
                    # misfire here because it would equal _prev_ncode.
                    self._set_phase("mowing", now)
                else:
                    # Could be a real app-initiated session or a scheduler
                    # flicker. Stay DOCKED until DP126/N-code confirms.
                    self._set_phase("candidate", now)

        # 3) DP1 falling edge (True -> False)
        if not first_update and not dp1 and self._prev_dp1:
            if self._phase == "map_save":
                self._set_phase("idle", now)       # map save finished
            elif self._phase == "mowing":
                self._set_phase("returning", now)  # physical return begins
            else:
                self._set_phase("idle", now)       # flicker / unconfirmed end

        # 4) Confirmation: DP126 increments only during real mowing. Promotes
        #    candidates, and self-corrects a wrong phase after HA restarts or
        #    brief DP1 blips (map save never increments DP126).
        if dp1 and area_incremented and self._phase in (
            "candidate",
            "idle",
            "map_save",
        ):
            self._set_phase("mowing", now)

        # 5) Failsafes
        if (
            self._phase == "returning"
            and now - self._phase_ts > RETURNING_TIMEOUT
        ):
            self._set_phase("idle", now)
        if (
            self._phase == "map_save"
            and now - self._phase_ts > MAP_SAVE_TIMEOUT
        ):
            self._set_phase("idle", now)

        self._prev_dp1 = dp1
        self._prev_ncode = ncode
        self._prev_area = area
        super()._handle_coordinator_update()

    # ── activity (pure read of the phase) ────────────────────────────────────

    @property
    def activity(self) -> LawnMowerActivity:
        dps = self._dps()
        dp1 = bool(dps.get(DP_TASK_ACTIVE, False))
        dp2 = bool(dps.get(DP_PAUSED, False))

        if dp1 and dp2:
            return LawnMowerActivity.PAUSED
        if self._phase == "returning":
            return LawnMowerActivity.RETURNING
        if self._phase == "mowing" and dp1:
            return LawnMowerActivity.MOWING
        # idle, candidate, map_save (and mowing without DP1) read as DOCKED
        return LawnMowerActivity.DOCKED

    # ── commands ─────────────────────────────────────────────────────────────

    async def async_start_mowing(self) -> None:
        """Start or resume mowing."""
        if self.activity == LawnMowerActivity.PAUSED:
            dp, val = CMD_RESUME
        else:
            dp, val = CMD_START
            # No N-code fires for HA-initiated starts; remember that WE
            # started this so the rising edge is trusted as real mowing.
            self._own_start_ts = time.monotonic()
        await self.coordinator.async_send_command(dp, val)

    async def async_pause(self) -> None:
        """Pause the mowing session."""
        dp, val = CMD_PAUSE
        await self.coordinator.async_send_command(dp, val)

    async def async_dock(self) -> None:
        """Stop mowing and return to base."""
        dp, val = CMD_DOCK
        await self.coordinator.async_send_command(dp, val)
