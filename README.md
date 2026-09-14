# CLI Alarm Clock

A terminal-based alarm clock built in Python using only the standard library.
No database, no external packages — just Python.

## How to Run

```bash
python main.py
```

## Features

- Create alarm with time, label, and day schedule
- List all alarms sorted by time
- Update existing alarm (time, label, days, enabled)
- Delete alarm with confirmation
- Alarms persist between sessions via JSON file
- Background thread checks alarms every second
- Thread-safe file reads and writes
- Graceful Ctrl+C shutdown

## Day Scheduling

```
once              → fires one time, disables itself after
daily             → fires every day
mon,wed,fri       → fires on specific weekdays only
```

Valid day codes: `mon tue wed thu fri sat sun`

## Usage

```
1) List alarms    → shows all alarms sorted by time
2) Create alarm   → time + label + day schedule
3) Update alarm   → change any field, blank = keep current
4) Delete alarm   → confirm with 'yes' before deleting
5) Quit           → clean shutdown
```

## Example

```
Time (HH:MM, 24h): 07:30
Label: Wake up
Days: daily
→ Created alarm a3f9b2c1 at 07:30 (Wake up)
```

## Design Decisions

**`AlarmStore` class with threading.Lock**
All reads and writes to `alarms.json` go through a single class.
A `threading.Lock` ensures the background checker thread and the
main (user-facing) thread never access the file simultaneously,
preventing data corruption.

**Atomic file write**
Alarms are written to a `.tmp` file first, then renamed using
`os.replace()` — atomic on both POSIX and Windows. If the process
crashes mid-write, the original file is never corrupted.

**`AlarmChecker` thread with `stop_event`**
Runs as a daemon thread. Uses `threading.Event.wait(1)` instead of
`time.sleep(1)` so it responds to `stop()` immediately on exit.
Checks once per minute-tick using `last_checked_minute` to prevent
the same alarm from firing multiple times in the same minute.

**`last_triggered` guard**
Stores the last ISO timestamp when an alarm fired. Prevents an alarm
from ringing more than once per minute even if the checker wakes
up multiple times.

**One-off vs recurring alarms**
`days = ["once"]` means the alarm fires once and auto-disables.
`days = ["mon","wed","fri"]` means it fires on those weekdays only.
`daily` expands to all 7 days at creation time.

**`prompt()` helper with `allow_blank_keep`**
Centralises the input → validate → retry loop in one function.
`allow_blank_keep` lets update flows return the existing value when
the user presses Enter, keeping the UX clean without duplicating logic.

**`created_at` field**
Stored on every alarm for traceability. Not used by the scheduler
but useful for debugging and future features like alarm history.

## File Structure

```
alarm_clock.py   → single file, all logic
alarms.json      → auto-created on first run (do not commit)
README.md        → this file
.gitignore       → excludes alarms.json and pycache
```

## Sample alarms.json

```json
[
  {
    "id": "a3f9b2c1",
    "time": "07:30",
    "label": "Wake up",
    "days": ["mon", "tue", "wed", "thu", "fri"],
    "enabled": true,
    "last_triggered": null,
    "created_at": "2026-09-14T07:00:00"
  },
  {
    "id": "b7d4e5f2",
    "time": "09:00",
    "label": "Standup",
    "days": ["once"],
    "enabled": false,
    "last_triggered": "2026-09-14T09:00:00",
    "created_at": "2026-09-14T08:00:00"
  }
]
```