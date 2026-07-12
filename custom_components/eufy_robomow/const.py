"""Constants for the Eufy Robomow integration."""

DOMAIN = "eufy_robomow"

# Tuya local protocol
DEFAULT_PORT = 6668
TUYA_VERSION = 3.5
POLL_INTERVAL = 10  # seconds — reduced for faster reverse engineering

# Generic sensor prefix for unmapped DPS
GENERIC_SENSOR_PREFIX = "dp"

# Speed options (used by select.py)
SPEED_SLOW = "slow"
SPEED_NORMAL = "normal"
SPEED_FAST = "fast"
SPEED_OPTIONS = [SPEED_SLOW, SPEED_NORMAL, SPEED_FAST]

# Config entry keys
CONF_DEVICE_ID = "device_id"
CONF_LOCAL_KEY = "local_key"
CONF_EUFY_EMAIL = "eufy_email"  # optional — enables cloud settings
CONF_EUFY_PASSWORD = "eufy_password"  # optional — enables cloud settings
CONF_DEVICE_NAME = "device_name"

# ── Cloud settings poll interval ───────────────────────────────────────────────
# Cloud settings are fetched at most once every N seconds (much slower than local).
# On repeated failures, the coordinator applies exponential backoff up to the cap.
CLOUD_POLL_INTERVAL = 300   # 5 minutes (base retry interval)
_CLOUD_MAX_BACKOFF  = 3600  # 1 hour   (maximum retry interval after repeated failures)

# ── Cloud settings data keys (stored in coordinator.data) ─────────────────────
# These keys coexist with numeric DPS keys; the "cloud_" prefix avoids collisions.
CLOUD_EDGE_MM = "cloud_edge_mm"
CLOUD_PATH_MM = "cloud_path_mm"
CLOUD_TRAVEL_SPEED = "cloud_travel_speed"
CLOUD_BLADE_SPEED = "cloud_blade_speed"
CLOUD_PAD_DIRECTION = "cloud_pad_direction"

# ── Edge distance (cm) ────────────────────────────────────────────────────────
# Matches app range: -15 to +15 cm, step 1 cm.
# Negative = mower cuts slightly beyond the border wire;
# positive = mower stays inside.  Stored as mm in DP155 field 3.
# NOTE: negative values use protobuf int32 encoding (10-byte varint).
# If the mower rejects negative values the field may use sint32 (zigzag);
# see cloud.py _varint_encode for the switch.
EDGE_DISTANCE_MIN = -15  # cm
EDGE_DISTANCE_MAX = 15  # cm
EDGE_DISTANCE_STEP = 1  # cm

# ── Path distance — app has exactly 3 options: 8 / 10 / 12 cm ────────────────
# Stored as mm in DP155 field 5.  Exposed as a SelectEntity.
# Updated to show inches as well since the app can be changed to that too
PATH_DISTANCE_MM: dict[str, int] = {
    "8 cm (3.1 in)": 80,
    "10 cm (3.9 in)": 100,
    "12 cm (4.7 in)": 120,
}
PATH_DISTANCE_OPTIONS: list[str] = list(PATH_DISTANCE_MM.keys())

# ── Pad direction (mowing path angle) — DP155 field 4 ─────────────────────────
# Stored as an integer in DP155 field 4, sub-field 2, inner field 1.
# Scale: 1 unit = 1 degree.  Reference direction: 0 = west (9 o'clock position).
# Confirmed live data points:
#   12 o'clock (north) ≈ 90–91
#   3  o'clock (east)  ≈ 178–180
# NOTE: DP154 is the zone-mow-mode signal, NOT the direction DP.
PAD_DIRECTION_MIN = 0  # degrees (west / 9 o'clock)
PAD_DIRECTION_MAX = 359  # degrees (full rotation)
PAD_DIRECTION_STEP = 1  # degrees

# ── DPS READ (status) ──────────────────────────────────────────────────────────
# Confirmed via live monitoring of Eufy E18 / Terramow S1200 (Tuya v3.5)

DP_TASK_ACTIVE = "1"  # bool  True = mowing, returning, or manual/remote control active
DP_PAUSED = "2"  # bool  True = session paused, False = actively moving
DP_BATTERY = "8"  # int   Battery level 0–100 %
DP_VOLUME = "26"  # int   Speaker volume 0–100 %
DP_CHILD_PROTECTION = "47"  # bool  True = child/pet protection active
DP_SIGNAL = "109"  # int   Signal strength (raw value; e.g. 50 → −50 dBm)
DP_CUT_HEIGHT = "110"  # int   Blade height in mm (e.g. 40)
DP_LAST_NOTIFICATION = "114"  # int  Most recent N-code notification from mower.
#       DP114 is updated whenever the mower emits an N-code event (verbal, toast,
#       push, or silent). Confirmed on E18 / Terramow S1200:
#       30  = N30  low battery, returning to charge  (unconfirmed channel)
#       31  = N31  child lock on                    (toast + verbal)
#       32  = N32  child lock off                   (toast + verbal)
#       41  = N41  mowing resumed after charge      (silent)
#       43  = N43  scheduled mowing started         (push notification)
#       65  = N65  cannot reach target area         (push notification)
#       66  = N66  mowing session ended              (push, "Task Completed")
#       76  = N76  returning to dock to charge      (silent)
#       96  = N96  schedule cancelled by user        (silent; provisional)
#       103 = N103 live camera on                   (toast)
#       104 = N104 live camera off                  (toast)
#       106 = N106 camera preparing                 (silent)
#       114 = N114 sunset, session cancelled        (toast + verbal)
#       127 = N127 loading system / boot            (verbal)
#       180 = N180 account session re-established    (login; provisional)
#       302 = N302 standby >12h, automatic shutdown  (dock left unplugged)
# See N_CODE_TEXT below for the human-readable lookup used by sensor.py.
DP_ERROR_CODE = "115"  # int  Error/obstacle code (E-code), sticky until next error.
#       201 = E0201 robot lifted off the ground
#       903 = E0903 robot trapped, obstacles need clearing
#       904 = E0904 cannot reach target area (pairs with N65)
#       909 = E0909 robot not on the lawn or a previously-created pathway
#       910 = E0910 not charging properly (observed cause: dock unplugged)
# See E_CODE_TEXT below for the human-readable lookup used by sensor.py.
DP_PROGRESS = "118"  # int   0–100 % progress of current action
#       0   = idle / mowing
#       1-99 = saving map or returning to base
#       100 = docked / fully done
DP_TOTAL_TIME = "125"  # int   Total mow time — ~6.6 sec/unit
#       36149 units ≈ 66h (app: 2d 18h) ✓
DP_AREA = "126"  # int   Mowed area counter (exact unit unconfirmed)
DP_RAIN_DETECTION = "101"  # bool  True = stop mowing when rain detected (writable setting)
DP_SMART_SUGGESTION = "132"  # bool  Smart suggestion for no-go zones
DP_REAL_LAWN_MAP = "133"  # bool  Real lawn map feature enabled
DP_NETWORK = "134"  # str   "Wifi" or "Cellular"
DP_MOW_YELLOW_GRASS = "141"  # bool  Allow mowing on yellow/dry grass

# ── N-code / E-code human-readable lookups ────────────────────────────────────
# Used by sensor.py's EufyNotificationTextSensor / EufyErrorTextSensor to turn
# the raw DP114 / DP115 codes into readable text, in-integration (no external
# HA template helpers required). Unrecognized codes fall back to "Code N" /
# "Unknown error N" so a brand-new code never shows a blank/broken sensor.
N_CODE_TEXT: dict[int, str] = {
    30: "Low battery — returning to charge",
    31: "Child lock turned on",
    32: "Child lock turned off",
    41: "Mowing resumed after charging",
    43: "Scheduled mowing started",
    65: "Cannot reach target area",
    66: "Mowing session ended",
    76: "Returning to dock",
    96: "Schedule cancelled",
    103: "Live camera turned on",
    104: "Live camera turned off",
    106: "Camera preparing",
    114: "Sunset — mowing cancelled for today",
    127: "System starting up",
    180: "Account reconnected",
    302: "Standby time exceeded 12 hours — shut down",
}

E_CODE_TEXT: dict[int, str] = {
    201: "Robot is lifted off the ground",
    903: "Robot is trapped — obstacles need clearing",
    904: "Cannot reach target area",
    909: "Robot not on lawn/pathway",
    910: "Not charging properly",
}

# ── Unmapped DPs for reverse engineering (all DPS exposed as sensors) ─────────
# These are automatically discovered and added as generic sensors.

# ── DPS WRITE (commands) ──────────────────────────────────────────────────────
# NOTE: These have NOT yet been confirmed by writing locally.
# They are the most likely candidates based on observed state changes.
# Test with: tinytuya Device.set_value(dp, value)

CMD_START = ("1", True)  # Set DP 1 = True  → start mowing
CMD_PAUSE = ("2", True)  # Set DP 2 = True  → pause session
CMD_RESUME = ("2", False)  # Set DP 2 = False → resume paused session
CMD_DOCK = ("1", False)  # Set DP 1 = False → stop & return to base
# (fallback: may need a dedicated dock DP)

# ── Cut height ────────────────────────────────────────────────────────────────
CUT_HEIGHT_MIN = 25  # mm (confirmed app minimum)
CUT_HEIGHT_MAX = 75  # mm (confirmed app maximum)
CUT_HEIGHT_STEP = 5  # mm

# ── Activity state logic ──────────────────────────────────────────────────────
# Confirmed via live DPS monitoring:
#
#  DP1 absent,  DP2 absent                    → DOCKED  (cold / never started)
#  DP1=True,    DP2=False,  DP118=0           → MOWING
#  DP1=True,    DP2=False,  DP118 5–99        → RETURNING (physical return to base)
#                                               ...OR map saving (see below)
#  DP1 absent/False                           → DOCKED  (no active session)
#  DP1=True,    DP2=True                      → PAUSED
#
# Map-save disambiguation:
#  When the mower physically docks at end-of-session, DP118 resets to 0 and DP1
#  briefly goes False then True again for map saving — during which DP118 climbs
#  again in the same 5–99 range.  Mid-session charge docks look similar but DP1
#  stays True throughout (no False transition).  The entity tracks this state
#  machine to suppress RETURNING during map saving (see _handle_coordinator_update).
#
RETURNING_THRESHOLD = 5  # DP118 ≥ this value while DP1 active = potentially RETURNING

# After the mower physically docks at end-of-session, DP1 briefly goes False then
# True again for map saving.  We suppress RETURNING during that map-save phase.
# If DP1 stays False longer than this many polls the session truly ended (e.g.
# sunset cancel); abandon the map-save expectation so the next real session is fresh.
# At POLL_INTERVAL=10 s this is 2 minutes.
MAP_SAVE_TIMEOUT_POLLS = 12
