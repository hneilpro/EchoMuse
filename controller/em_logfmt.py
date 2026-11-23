"""
em_logfmt.py — how controller log lines are stamped and summarised.

Split out for the reason `em_loglevel` is: the test suite cannot import
em_controller, so a formatting decision living there is a decision nobody
can test. Everything here is pure rendering over the live `logging`
module, which needs none of the aiohttp/zeroconf/openwakeword stack.

This module is deliberately ADDITIVE. It changes no upstream format
string and no upstream behaviour: `_LOG_FORMAT` in em_controller stays
exactly as written, and the hooks there are two lines — install the
formatter, log the startup summary. A merge from upstream that touches
either hook line conflicts in one place, loudly, rather than silently
undoing the timestamps.

TIMESTAMPS CARRY THEIR TIMEZONE, AND MILLISECONDS
-------------------------------------------------
The stock `%(asctime)s` renders container-local time with no offset and
no sub-second part: `2026-11-19 01:41:34`. The container runs UTC and
the household runs America/New_York, so every log line carried an
unstated five-hour ambiguity, and correlating a controller line with a
device or Home Assistant event meant guessing which clock wrote it.
That ambiguity cost real diagnosis time on 2026-11-19, chasing a TTS
fault across two logs that were five hours apart on paper and seconds
apart in fact.

`LocalTzFormatter` renders `2026-11-19 01:41:34.123-05:00`: local time
(as the container's TZ says local is), milliseconds, and the numeric
UTC offset in force AT THAT INSTANT, so a line either side of a DST
change still states the offset it was written under. The offset is
derived per record rather than cached at startup for exactly that
reason.

THE STARTUP LINE STATES THE SETTINGS IN FORCE
---------------------------------------------
`em_loglevel.effective()` exists for the support bundle, but a person
tailing a live log had no equivalent: the levels line in em_controller
only appears when LOG_LEVELS asked for an override, so a plain tail
could not say whether a missing line was a fault or a setting. The
startup summary always logs the effective levels and the local timezone
once, so the head of any log tail self-describes both.

THE [TURN] SUFFIX NAMES THE DEVICE AND THE WAKE BAR
---------------------------------------------------
The [TURN] line is the one line a slow or failed turn is diagnosed
from, and it named neither the device that took the turn — on a
multi-Echo household every line had to be correlated by timestamp
alone — nor the wake bar the detection crossed, though wake_info
already carried model and threshold to the database. `turn_suffix`
renders those as trailing fields, appended rather than interleaved,
so anything already parsing the existing fields is undisturbed.

NOTHING SECRET IS RENDERED HERE. The suffix carries a device id (the
identifier every other controller log line already prefixes), a model
name and numbers. No tokens, no credentials, no transcript — the STT
text in the [TURN] line is upstream's existing choice, untouched.
"""

from __future__ import annotations

import datetime
import logging

import em_loglevel


class LocalTzFormatter(logging.Formatter):
    """
    A Formatter whose asctime is local time with milliseconds and offset.

    Only formatTime is overridden; the format string, and therefore every
    other field, is whatever the caller passed — in the controller's case
    the unchanged upstream `_LOG_FORMAT`.
    """

    def formatTime(self, record: logging.LogRecord,
                   datefmt: str | None = None) -> str:
        when = datetime.datetime.fromtimestamp(record.created).astimezone()
        base = when.strftime(datefmt or "%Y-%m-%d %H:%M:%S")
        millis = int(record.msecs)
        offset = when.strftime("%z")  # +HHMM / -HHMM, empty only for naive
        if offset:
            offset = f"{offset[:3]}:{offset[3:]}"
        return f"{base}.{millis:03d}{offset}"


def install(fmt: str) -> None:
    """
    Put LocalTzFormatter on every handler the root logger currently has.

    Called from em_controller AFTER the log ring is installed, so the
    in-memory ring a support bundle carries is stamped the same way as
    the console — two differently-stamped copies of the same line would
    recreate, inside one bundle, the ambiguity this module exists to end.
    Handlers attached later keep the stock formatter; the controller
    attaches all of its own before this runs.
    """
    formatter = LocalTzFormatter(fmt)
    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter)


def startup_summary() -> str:
    """
    One line stating the log levels in force and the local timezone.

    Levels come from em_loglevel.effective() — what the live loggers are
    actually set to, not what LOG_LEVELS asked for. The timezone is the
    one the timestamps in this log are rendered in, stated as its name
    when the system knows one and always as the current UTC offset.
    """
    levels = ", ".join(
        f"{name}={level}" for name, level in em_loglevel.effective().items()
    )
    now = datetime.datetime.now().astimezone()
    offset = now.strftime("%z")
    if offset:
        offset = f"{offset[:3]}:{offset[3:]}"
    tzname = now.tzname() or "local"
    return (
        f"Log levels effective: {levels} "
        f"| local timezone: {tzname} (UTC{offset})"
    )


def turn_suffix(device_id: str = "", wake_info: dict | None = None) -> str:
    """
    The trailing [TURN] fields: which device, and the wake bar it crossed.

    Returns a leading-space string, or "" when there is nothing to add —
    a button turn on an unnamed trace renders exactly the upstream line.
    Wake score is NOT repeated here: it already rides in the trigger
    (`wakeword(0.522)`). Model, threshold and noise floor are the fields
    that were previously only in the database row.
    """
    parts: list[str] = []
    if device_id:
        parts.append(f"device={device_id}")
    wi = wake_info or {}
    if wi.get("model"):
        parts.append(f"wake_model={wi['model']}")
    if wi.get("threshold") is not None:
        parts.append(f"wake_threshold={wi['threshold']}")
    if wi.get("noise_floor") is not None:
        parts.append(f"noise_floor={wi['noise_floor']}")
    return (" " + " ".join(parts)) if parts else ""
