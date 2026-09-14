#!/usr/bin/env python3
"""
CLI Alarm Clock
================
Features:
  - Create, update, delete, and list alarms
  - Alarms persisted to a local JSON file (no database dependency)
  - A background thread continuously checks for due alarms
  - A threading.Lock guards all reads/writes to the JSON file so that
    the background thread and the main (user-facing) thread never
    corrupt each other's data
  - Input validation for every field the user provides

Run:
    python main.py
"""

import json
import os
import re
import sys
import time
import threading
import uuid
from datetime import datetime

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alarms.json")

VALID_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")  # HH:MM, 24-hour


# --------------------------------------------------------------------------
# Persistence layer
# --------------------------------------------------------------------------
class AlarmStore:
    """
    Handles all reading/writing of alarms.json.

    A single threading.Lock is shared by every method so that the
    background 'checker' thread and the main thread (handling user
    CRUD operations) can never read/write the file at the same time
    and corrupt it.
    """

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self.path):
            with self.lock:
                with open(self.path, "w") as f:
                    json.dump([], f)

    def _read(self):
        # Assumes caller already holds self.lock
        try:
            with open(self.path, "r") as f:
                content = f.read().strip()
                if not content:
                    return []
                return json.loads(content)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _write(self, alarms):
        # Assumes caller already holds self.lock
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(alarms, f, indent=2)
        os.replace(tmp_path, self.path)  # atomic on POSIX & Windows

    # ---- public, thread-safe API -----------------------------------

    def list_alarms(self):
        with self.lock:
            return self._read()

    def get_alarm(self, alarm_id):
        with self.lock:
            alarms = self._read()
            for a in alarms:
                if a["id"] == alarm_id:
                    return a
            return None

    def add_alarm(self, alarm_time, label, days, enabled=True):
        with self.lock:
            alarms = self._read()
            new_alarm = {
                "id": uuid.uuid4().hex[:8],
                "time": alarm_time,
                "label": label,
                "days": days,          # ["once"] or subset of VALID_DAYS
                "enabled": enabled,
                "last_triggered": None,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            alarms.append(new_alarm)
            self._write(alarms)
            return new_alarm

    def update_alarm(self, alarm_id, **fields):
        with self.lock:
            alarms = self._read()
            for a in alarms:
                if a["id"] == alarm_id:
                    a.update(fields)
                    self._write(alarms)
                    return a
            return None

    def delete_alarm(self, alarm_id):
        with self.lock:
            alarms = self._read()
            new_alarms = [a for a in alarms if a["id"] != alarm_id]
            if len(new_alarms) == len(alarms):
                return False
            self._write(new_alarms)
            return True

    def mark_triggered(self, alarm_id, timestamp):
        with self.lock:
            alarms = self._read()
            for a in alarms:
                if a["id"] == alarm_id:
                    a["last_triggered"] = timestamp
                    # one-off alarms disable themselves after firing
                    if a["days"] == ["once"]:
                        a["enabled"] = False
                    self._write(alarms)
                    return


# --------------------------------------------------------------------------
# Background checker thread
# --------------------------------------------------------------------------
class AlarmChecker(threading.Thread):
    """
    Runs in the background, waking up once per second to see whether
    any enabled alarm matches the current time. When it does, it
    prints/rings a notification and records that it fired.
    """

    def __init__(self, store: AlarmStore, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.store = store
        self.stop_event = stop_event

    def run(self):
        last_checked_minute = None
        while not self.stop_event.is_set():
            now = datetime.now()
            current_minute_key = now.strftime("%Y-%m-%d %H:%M")
            current_time_str = now.strftime("%H:%M")
            current_day = VALID_DAYS[now.weekday()]

            # Only evaluate once per minute-tick to avoid re-firing repeatedly
            if current_minute_key != last_checked_minute:
                last_checked_minute = current_minute_key
                for alarm in self.store.list_alarms():
                    if not alarm.get("enabled"):
                        continue
                    if alarm["time"] != current_time_str:
                        continue
                    if "once" in alarm["days"] or current_day in alarm["days"]:
                        self._ring(alarm)
                        self.store.mark_triggered(alarm["id"], now.isoformat(timespec="seconds"))

            self.stop_event.wait(1)  # check roughly every second

    @staticmethod
    def _ring(alarm):
        # \a = terminal bell (best-effort; silent if terminal doesn't support it)
        sys.stdout.write("\a")
        sys.stdout.flush()
        print(f"\n[ALARM] {alarm['time']} - {alarm['label']}  (id: {alarm['id']})\n")


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------
def validate_time(value: str) -> str:
    value = value.strip()
    if not TIME_RE.match(value):
        raise ValueError("Time must be in 24-hour HH:MM format, e.g. 07:30 or 21:05.")
    return value


def validate_label(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Label cannot be empty.")
    if len(value) > 60:
        raise ValueError("Label must be 60 characters or fewer.")
    return value


def validate_days(value: str):
    value = value.strip().lower()
    if value in ("", "once"):
        return ["once"]
    if value == "daily":
        return VALID_DAYS.copy()
    days = [d.strip() for d in value.split(",") if d.strip()]
    invalid = [d for d in days if d not in VALID_DAYS]
    if invalid:
        raise ValueError(
            f"Invalid day(s): {', '.join(invalid)}. "
            f"Use comma-separated values from {VALID_DAYS}, 'daily', or 'once'."
        )
    if not days:
        raise ValueError("Days list cannot be empty.")
    return sorted(set(days), key=VALID_DAYS.index)


def prompt(msg, validator=None, allow_blank_keep=None):
    """
    Prompt the user, re-asking on invalid input.
    If allow_blank_keep is not None, an empty response returns that value
    unchanged (used for 'update' flows where blank = keep current value).
    """
    while True:
        raw = input(msg).strip()
        if raw == "" and allow_blank_keep is not None:
            return allow_blank_keep
        if validator is None:
            return raw
        try:
            return validator(raw)
        except ValueError as e:
            print(f"  Invalid input: {e}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def format_alarm_row(a):
    days = "Once" if a["days"] == ["once"] else ",".join(d.capitalize() for d in a["days"])
    status = "ON " if a["enabled"] else "OFF"
    return f"{a['id']:<10} {a['time']:<6} {status:<4} {days:<20} {a['label']}"


def cmd_list(store: AlarmStore):
    alarms = store.list_alarms()
    if not alarms:
        print("No alarms set.")
        return
    alarms.sort(key=lambda a: a["time"])
    print(f"\n{'ID':<10} {'TIME':<6} {'ON?':<4} {'DAYS':<20} LABEL")
    print("-" * 70)
    for a in alarms:
        print(format_alarm_row(a))
    print()


def cmd_create(store: AlarmStore):
    print("\n-- Create a new alarm --")
    alarm_time = prompt("Time (HH:MM, 24h): ", validate_time)
    label = prompt("Label: ", validate_label)
    days = prompt(
        "Days (comma-separated mon..sun, 'daily', or 'once') [once]: ",
        validate_days,
    )
    alarm = store.add_alarm(alarm_time, label, days)
    print(f"Created alarm {alarm['id']} at {alarm['time']} ({label}).\n")


def cmd_update(store: AlarmStore):
    print("\n-- Update an alarm --")
    alarm_id = prompt("Alarm ID to update: ").strip()
    existing = store.get_alarm(alarm_id)
    if not existing:
        print(f"No alarm found with id '{alarm_id}'.\n")
        return

    print(f"Current: {format_alarm_row(existing)}")
    print("Press Enter to keep the current value for any field.\n")

    new_time = prompt(f"Time [{existing['time']}]: ", validate_time, allow_blank_keep=existing["time"])
    new_label = prompt(f"Label [{existing['label']}]: ", validate_label, allow_blank_keep=existing["label"])
    current_days_str = "once" if existing["days"] == ["once"] else ",".join(existing["days"])
    new_days = prompt(
        f"Days [{current_days_str}]: ", validate_days, allow_blank_keep=existing["days"]
    )
    enabled_str = prompt(
        f"Enabled? y/n [{'y' if existing['enabled'] else 'n'}]: ",
        allow_blank_keep="y" if existing["enabled"] else "n",
    )
    enabled = enabled_str.strip().lower() in ("y", "yes", "true", "1")

    updated = store.update_alarm(
        alarm_id, time=new_time, label=new_label, days=new_days, enabled=enabled
    )
    if updated:
        print(f"Updated alarm {alarm_id}.\n")
    else:
        print("Update failed (alarm may have been deleted concurrently).\n")


def cmd_delete(store: AlarmStore):
    print("\n-- Delete an alarm --")
    alarm_id = prompt("Alarm ID to delete: ").strip()
    confirm = prompt(f"Type 'yes' to confirm deleting '{alarm_id}': ").strip().lower()
    if confirm != "yes":
        print("Cancelled.\n")
        return
    if store.delete_alarm(alarm_id):
        print(f"Deleted alarm {alarm_id}.\n")
    else:
        print(f"No alarm found with id '{alarm_id}'.\n")


MENU = """
=========== CLI Alarm Clock ===========
1) List alarms
2) Create alarm
3) Update alarm
4) Delete alarm
5) Quit
========================================
"""


def main():
    store = AlarmStore(DATA_FILE)
    stop_event = threading.Event()
    checker = AlarmChecker(store, stop_event)
    checker.start()

    print(f"Alarm data file: {DATA_FILE}")
    print("Background checker thread started. Alarms will ring even while you use the menu.")

    try:
        while True:
            print(MENU)
            choice = input("Choose an option (1-5): ").strip()

            if choice == "1":
                cmd_list(store)
            elif choice == "2":
                cmd_create(store)
            elif choice == "3":
                cmd_update(store)
            elif choice == "4":
                cmd_delete(store)
            elif choice == "5":
                print("Goodbye!")
                break
            else:
                print("Invalid choice, please enter a number from 1 to 5.\n")
    except KeyboardInterrupt:
        print("\nInterrupted. Shutting down...")
    finally:
        stop_event.set()
        checker.join(timeout=2)


if __name__ == "__main__":
    main()