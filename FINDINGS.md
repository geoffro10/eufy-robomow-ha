# Eufy Robomow — Protocol Findings

Reverse-engineered from live monitoring of an E18 / Terramow S1200 (Tuya local
protocol v3.5 + Tuya cloud mobile API), confirmed via stopwatch verification,
controlled experiments, and ground-truth app actions. Everything below is
observed, not assumed — see "Confidence" notes where relevant.

This document is the source of truth for the integration's protocol
understanding. If it conflicts with a comment in the code, the code is
probably stale — update the code to match this file.

---

## 1. Core status DPs (local Tuya protocol)

| DP | Type | Meaning |
|----|------|---------|
| 1 | bool | **Task active.** True during mowing, physical returning, manual/remote control, and map saving. NOT a simple "docked" flag — see §4 State Machine. |
| 2 | bool | **Paused.** True when paused mid-session (including mid-session dock-to-charge). |
| 8 | int | Battery 0–100%. Real-time; draws down even for non-mowing loads (e.g. camera defogger). |
| 26 | int | Speaker volume 0–100%. |
| 36 | str | Language (e.g. `"english"`). |
| 47 | bool | Child/pet lock protection. |
| 101 | bool | Stop-on-rain-detection setting. |
| 109 | int | Signal strength, raw. Real value = `-raw` dBm (e.g. `54` → -54 dBm). |
| 110 | int | Cut height, mm. Mirrors DP155 field 1 (cloud). |
| 114 | int | **Last N-code notification.** Not a state — see §2. |
| 115 | int | **Last E-code (error) notification.** Sticky — holds the last error until a new one occurs, does not clear itself. See §3. |
| 118 | int | **Map-saving progress, 0→100.** Climbs ONLY after the mower has already physically docked (after a DP1 False→True transition). Does **not** track the physical return journey — see §4. Also climbs in-place (from 100, not 0) on "stop without saving progress." |
| 125 | int | Total mow time, ~6.6 sec/unit. Updates in ~60-unit batches roughly hourly. Can jump backward after a mower reboot (known `total_increasing` sensor warning in HA — cosmetic, not a bug in the integration). |
| 126 | int | **Mowed area counter.** Increments every 30–90s during genuine mowing; never increments during scheduler flickers or map saving. This is the single strongest confirmation signal for "is the mower actually cutting grass right now." |
| 131 | int | Always `90` in every observation. Static config, meaning unconfirmed (possibly a default pad-direction fallback). |
| 132 | bool | Smart no-go-zone suggestions enabled. |
| 133 | bool | "Real lawn map" feature enabled. |
| 134 | str | Network — `"Wifi"` / `"Cellular"` / `None`. Flickers to `None` briefly and unreliably during mowing; not usable as a connectivity signal. |
| 141 | bool | Allow mowing on yellow/dry grass. |
| 137, 139, 140, 146, 170, 178, 181 | — | Never observed to change during any mowing, pausing, charging, or manual-control test. Read-only configuration values. **Exception:** DP146 toggled 0↔1 multiple times, always within seconds of an app *settings* edit (cut height, edge distance, blade speed) — provisional label: **settings-edit/sync-in-progress flag.** Polarity not fully pinned down (seen both 0→1 and 1→0 during edits). |
| 171–177, 182–185 | int | Always `-2147483647` (INT32_MIN) — null/unset sentinel. |

## 2. N-codes (DP114) — notification register

DP114 is **not** a live-view or camera state (an earlier assumption in the
code was wrong). It is a general-purpose notification register: every time
the mower emits an event, DP114 is set to that event's numeric N-code. It is
sticky — it holds the last code until the next event, so code consuming it
must react to *changes*, not raw values.

| Code | Meaning | Delivery channel | Confidence |
|---|---|---|---|
| 30 | Low battery, returning to charge | Unconfirmed type | High (timing-correlated with a real low-battery return) |
| 31 | Child lock on | Toast + verbal | Confirmed (toast text: "Child lock on") |
| 32 | Child lock off | Toast + verbal | Confirmed (toast text: "Child lock temporarily off") |
| 41 | Mowing resumed after a mid-session charge | Silent | Confirmed, repeatedly |
| 43 | Scheduled mowing task started | Push notification | Confirmed, repeatedly |
| 65 | Cannot reach target area (pairs with E-code 904) | Push notification | Confirmed |
| 66 | Session ended | Push ("Task Completed") | Confirmed — fires at the end of **every** session, not only failures |
| 76 | Returning to dock (fires ~70s before the physical journey completes) | Silent | Confirmed |
| 96 | Schedule cancelled by user | Silent | Provisional — single observation, coincided with a mode change in the same window |
| 103 | Live camera on | Toast | Confirmed |
| 104 | Live camera off | Toast | Confirmed |
| 106 | Camera preparing (brief, precedes 103; not always present) | Silent | Confirmed |
| 114 | Sunset — pending task cancelled | Toast + verbal | Confirmed |
| 127 | Loading system / boot ("Loading system") | Verbal | Confirmed |
| 180 | Account session re-established (app login) | — | Provisional — single observation, coincided with a forced re-login |

**HA/local-Tuya-initiated commands (`CMD_START` etc.) generate NO N-code.**
DP114 simply stays at whatever value it last held. The integration's own
`OPTIMISTIC_START_WINDOW` trick (remembering that *we* just sent CMD_START)
exists specifically to work around this gap.

## 3. E-codes (DP115) — error register

Same design as DP114, but for error/fault conditions specifically. Also sticky.

| Code | Meaning |
|---|---|
| 201 | E0201 — robot lifted off the ground |
| 903 | E0903 — robot trapped, obstacles need clearing |
| 904 | E0904 — cannot reach target area (pairs with N65) |

## 4. Activity state machine

### The central discovery: DP118 is map-save progress, not return progress

Stopwatch-verified, repeatedly:

```
DP1: True -> False        session ends, mower physically starts driving home
      (~1–2 minutes of real, unmonitored travel time)
DP1: False -> True        mower has ARRIVED at the dock
DP118: resets to 0, then climbs 0→100   ← this is MAP SAVING, mower already docked
DP1: True -> False        map save complete, fully done
```

The mower is **already sitting on the dock** for the entire DP118 climb. Using
DP118 to detect "returning" (an earlier design) causes the integration to
falsely report RETURNING for the ~30–90 seconds map-saving actually takes,
right after the mower is already home.

### Correct signal set

- **DP1 True→False after a confirmed mowing session** = physical return journey begins.
- **DP1 False→True while in the returning phase** = physical arrival; the DP118 climb that follows is map saving — display should stay DOCKED, not RETURNING or MOWING.
- **DP1 True→False while in map-save phase** = map save finished, fully idle.
- **DP126 incrementing** = the strongest possible confirmation that real mowing is happening right now.
- **N43 / N41** arriving on the same poll as a DP1 rising edge = trustworthy signal that this is a genuine start (scheduled or resume-after-charge), usable even before DP126 has had a chance to move.
- **Mid-session charge:** DP1 stays **True** throughout — only DP2 toggles (pause/resume for charging). No DP1 edge occurs, so state-machine logic keyed on DP1 edges is naturally unaffected — this turned out to be a robustness feature of the design, not something needing special-cased handling.
- **Post-session "scheduler flicker":** DP1 briefly goes True (30s–5min) with no N-code change and no DP126 increment, then drops again. Purely a housekeeping/scheduler artifact — must be displayed as DOCKED, not MOWING. Suppressed by requiring at least one DP126 increment (or a very short grace window) before trusting a DP1 rise as genuine mowing.
- **HA-initiated start:** generates no N-code. The integration tracks its own `CMD_START` timestamp and trusts the next DP1 rising edge within a short window as genuine.

### Previously-suspected limitation, now confirmed fixed

An earlier version of this document noted that CMD_DOCK might show DOCKED
instantly rather than RETURNING, since it sets DP1=False immediately while
the mower still takes 1-2 minutes to physically travel home. Live-tested
2026-07-04: pressing Dock from the HA lawn_mower entity while actively
mowing correctly transitions the entity to RETURNING (not DOCKED), because
the DP1 True→False edge is evaluated the same way regardless of what
caused it — command or natural session end. The phase-machine rewrite
resolved this as a side effect; no further work needed here.

## 5. Zone table (DP122 / DP107 / schedule data)

Confirmed by controlled single-zone schedule creation and reading the
resulting DP122 zone byte for each, cross-checked against a second
"every zone except X" record:

| Zone # | Name |
|---|---|
| 7 | Front Yard |
| 8 | Road |
| 9 | Side House |
| 10 | Driveway |
| 11 | Back Yard 2 |
| 12 | Back Yard 1 |
| 13 | Back Yard 3 |

Zone numbers are the mower's own internal IDs (not app-display order) and
survive zone renaming. DP160 (`"02344139"`) is a separate zone/lawn-map
identifier that does **not** change between zones — it's a map ID, not a
per-zone selector.

**DP107** (cloud, live task descriptor) does *not* encode which zone is
active — confirmed by mowing two different zones and observing the same
value in its `f1` sub-field both times. Current best read of DP107:

- `f1 = 10` while a zone-mowing task is active (any zone)
- `f1 = 1` while a return-to-base task is active
- `f2` — phase/sub-step counter, not yet fully mapped (values observed
  changing within a single session even while `f1` stayed constant — likely
  an internal step or waypoint index)
- `f3 = 1` — generic task-active flag
- Absent / `{f4: N}` — idle, with `N` as an idle sub-stage counter

## 6. DP122 — schedule storage (fully decoded)

DP122 is a protobuf blob holding all mowing schedules. **Confirmed
report-only**: a byte-identical write-back round-trip succeeds (revision
counter changes, content doesn't), but a modified blob with an injected
schedule record is silently discarded by the device and DP122 is
regenerated from the mower's own internal schedule store on the next poll.
**The write channel for schedules is not DP122** — see §8.

### Structure

```
DP122
├─ field 2:  revision counter (varint) — NOT monotonic, appears to be a
│            hash/nonce; changes on every schedule edit, including ones
│            that don't change content (e.g. an identity write-back)
├─ field 4:  schedule container
│   └─ field 1 (repeated): one entry per schedule record
│       ├─ field 1: record ID (varint; 0 is omitted when absent — the
│       │           first-ever record has no explicit ID byte). IDs are
│       │           stable and assigned at creation; they do NOT reflect
│       │           position — records can and do reorder within the
│       │           container between polls.
│       ├─ field 2: constant `2` in every observation (purpose unclear —
│       │           possibly a schedule-type/version tag)
│       └─ field 5: record body
│           ├─ field 1: timing
│           │   ├─ field 1: packed day-of-week bytes, Mon=1 … Sun=7
│           │   │           (e.g. bytes [1,3,5,7] = Mon/Wed/Fri/Sun)
│           │   ├─ field 2: start time {f1: hour, f2: minute}
│           │   │           (minute field OMITTED when :00)
│           │   ├─ field 3: end time {f1: hour, f2: minute} (same rule)
│           │   ├─ field 4: constant `1` in every observation
│           │   └─ field 5: `1` = one-time schedule (absent on recurring
│           │               weekly schedules)
│           ├─ field 2: constant `1` in every observation
│           └─ field 3: **zone list** — raw packed bytes, one byte per
│                       zone number, in mow order (e.g. bytes [10,8,9] =
│                       Driveway then Road then Side House)
├─ field 2 (top-level, after the container): sunrise {f1: hour, f2: minute}
└─ field 3 (top-level): sunset {f1: hour, f2: minute}
      — this is almost certainly the trigger source for N114 (sunset
        cancellation): the mower checks its own clock against this
        cached sunset time.
```

### Special record: "mow everything"

A schedule targeting the whole lawn (no specific zone selected in the app)
encodes with an **empty zone-list byte** for field 3, and uses a structurally
different wrapper (nested one level deeper, under an outer field 4 rather
than the flat field-3-zone-list shape every specific-zone record uses). Not
just "a zone list with nothing in it" — a distinct record shape.

### Special record: "every zone except X"

Confirmed by creating a "mow everything except Back Yard 1 (zone 12)"
schedule: the zone list contains every zone number **except** the excluded
one (i.e. the included zones are listed explicitly; exclusion is achieved
by omission, there is no separate "exclude" flag/field).

## 7. DP155 — cloud default settings (write-safe)

Base64 protobuf blob, fetched/written via the Tuya mobile API
(`tuya.m.device.dp.get` / `tuya.m.device.dp.publish`).

```
field 1 (msg)    : cut height, mm — {1: mm}. Mirrors DP110.
                   IMPORTANT: earlier code treated this as a fixed constant
                   {1: 40} and rebuilt it on every write. That was wrong and
                   destructive — it silently reset cut height to 40mm on
                   every cloud write of ANY other setting. Confirmed via
                   live testing 2026-07-02.
field 2 (msg)    : travel speed — empty submsg = slow, {1:1} = normal,
                   {1:2} = fast
field 3 (msg)    : edge distance, mm, signed. Negative = mower cuts beyond
                   the boundary wire; positive = stays inside. Negative
                   values use two's-complement 64-bit varint encoding (10
                   bytes). ZERO is encoded as an EMPTY submessage (length 0),
                   matching the app's own convention — NOT {1: 0}.
field 4 (msg)    : pad direction — {f2: {f1: angle_degrees}, f3: ?, f5: ?}.
                   0° = west (9 o'clock), 90° = north, 180° = east, 270° =
                   south. f3 and f5 are NOT fixed constants — f5 was
                   observed at -306 on a live device — they must be
                   preserved byte-for-byte on any write, never regenerated.
field 5 (msg)    : path distance, mm — {1: mm}. Zero likely follows the
                   same empty-submessage convention as field 3 (unconfirmed
                   — path distance has no zero option in the app UI).
field 6 (msg)    : blade speed — same encoding as field 2 (travel speed).
field 7 (varint) : loosely tracks path_mm but the device doesn't always
                   keep it in sync — treat as advisory only, not authoritative.
```

**Units:** the blob always stores millimetres regardless of the app's
display unit. Confirmed: app entry "-10" while in cm mode wrote -100mm; app
entry "-2 in" while in inch mode wrote -50mm (2in ≈ 50.8mm, rounded).

**DP155 is a DEFAULT-settings profile, not per-zone.** All observed writes
happened only while editing the app's *default* settings screen. Per-zone
customized settings (if the app supports them per-zone) likely live
elsewhere — possibly folded into the DP122/DP150 territory — and are not
yet mapped.

**Write strategy (implemented in `cloud.py` `_patch_dp155`):** never rebuild
this blob from a settings model. Fetch the current blob, parse it into
raw (field, wire-type, bytes) tuples, replace ONLY the field(s) actually
being changed, and re-serialize everything else byte-for-byte untouched.
Verified against real device blobs: a round-trip with zero changes is
byte-identical, and a single-field patch (e.g. blade speed) touches
exactly that one field, leaving cut height, edge distance, and the
mysterious field-4 sub-fields completely undisturbed.

## 8. Schedule write channel — status: NOT FOUND (documented dead ends)

Extensive effort went into finding how the app actually *writes* a new
schedule (as opposed to reading the result via DP122, which works fine).
Every avenue investigated and ruled out, so future effort isn't wasted
repeating them:

- **DP122 direct write** — confirmed report-only (§6). Publishing a modified
  blob succeeds at the API level but the device silently discards the
  content and regenerates DP122 from its own internal store.
- **Tuya standard timer API** (`tuya.m.timer.*` family) — the endpoints
  exist and are reachable with our authenticated session, but
  `tuya.m.timer.category.list` returns an empty list and every
  `tuya.m.timer.group.list` category probed returns empty, despite real
  schedules being active on the device at the time. Schedules are not
  stored as generic Tuya timers.
- **Device command/DP log endpoints** (`tuya.m.device.dp.log` and five
  similar candidate action names) — all return `API_OR_API_VERSION_WRONG`;
  none of these endpoints exist on this backend.
- **MITM on the live app** (rooted Pixel 7 Pro, HTTP Toolkit + Frida
  pinning bypass, traffic confirmed reaching `a1-us.iotbing.com`, the
  correct Tuya backend host) — traffic capture succeeded at the TLS layer,
  but Tuya's mobile API adds a **second, app-level encryption layer** on
  top (`{"t":…, "sign":…, "result": <ciphertext>}`). Several plausible
  key-derivation schemes (MD5 of session id / uid / username, tried against
  AES-ECB/GCM/CTR/CFB/OFB) were tested against real captured ciphertext and
  none decrypted it. The actual key derivation is presumably in the app's
  native/Java crypto code and would require decompilation (e.g. `jadx`) to
  read directly — not yet attempted.

**Practical consequence:** schedules can currently only be created, edited,
or deleted through the Eufy app. The integration can read and fully
interpret the resulting schedule state (zones, times, days, one-time vs.
recurring) but cannot originate a schedule change itself. Manual
mow/pause/dock commands (DP1/DP2 writes) are fully supported and unaffected
by this limitation.

## 9. Miscellaneous confirmed odds and ends

- **DP125 backward jumps**: observed after what was likely a mower firmware
  reboot. Triggers HA's `total_increasing` sensor warning. Cosmetic only —
  not indicative of an integration bug, but worth a defensive fix (ignore
  decreases) if the warning noise becomes bothersome.
- **DP109 (signal strength) transient dropout to `0`**: seen briefly during
  active mowing; self-corrects within one poll. Not a connectivity issue.
- **DP134 (`"Wifi"`/`None`) flicker**: happens routinely during mowing
  (once every few minutes), self-corrects within ~10s. Not a reliable
  connectivity signal — do not build automations on it.
- **Manual/remote control** (app joystick driving) sets DP1=True with no
  distinguishing N-code and no DP126 movement (the mower isn't cutting
  grass while just being driven). The integration cannot currently tell
  "manual driving" apart from "about to start mowing" other than by the
  eventual DP126 confirmation (or lack of it).
- **"Stop without saving progress"** (an app option when cancelling
  mid-mow): triggers an in-place map save — DP118 climbs from a non-zero
  value (not reset to 0 first) while DP1 stays True the whole time, then
  DP1 drops once. Structurally different from the normal end-of-session
  sequence; the state machine's `area_incremented` / phase logic handles
  it correctly without special-casing, since DP126 simply stops
  incrementing regardless of which shutdown path triggered it.

---

*Last updated from live findings through 2026-07-04. Confirmed against a
full end-to-end validated test run (scheduled start → mowing → session
end → physical return → dock → map save → idle) with zero spurious state
transitions.*
