"""
em_logfmt: timestamps that state their timezone, a startup line that
states the settings, and a [TURN] line that names its device.

The decisions these pin are the ones that cost diagnosis time on
2026-11-19: a controller log in unstated UTC read against a household
in America/New_York, and [TURN] lines that named neither the device
nor the wake threshold the detection had crossed. Each rendering is
pinned as a string shape, not a snapshot, so a formatter refactor that
keeps the contract passes and one that drops the offset fails.
"""

import datetime
import logging
import os
import re
import time

import pytest

import em_logfmt
import em_turn_record

STAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$"
)


def _record(created: float) -> logging.LogRecord:
    record = logging.LogRecord(
        "echomuse", logging.INFO, __file__, 1, "hello", (), None
    )
    record.created = created
    record.msecs = (created - int(created)) * 1000
    return record


# ── The timestamp ──────────────────────────────────────────────────────────

def test_timestamp_carries_milliseconds_and_offset():
    created = datetime.datetime(
        2026, 11, 19, 1, 41, 34,
        tzinfo=datetime.timezone.utc).timestamp() + 0.123
    rendered = em_logfmt.LocalTzFormatter("%(asctime)s").formatTime(
        _record(created))
    assert STAMP.match(rendered), rendered
    # The offset is the local zone's offset AT THAT INSTANT, not a
    # constant — either side of a DST change must state its own.
    expected = datetime.datetime.fromtimestamp(created) \
        .astimezone().strftime("%z")
    assert rendered.endswith(f"{expected[:3]}:{expected[3:]}")


@pytest.mark.skipif(not hasattr(time, "tzset"),
                    reason="tzset is POSIX-only")
def test_timestamp_renders_the_configured_zone():
    """Under TZ=America/New_York a January noon UTC is 07:00 at -05:00."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    try:
        created = datetime.datetime(
            2026, 1, 15, 12, 0, 0,
            tzinfo=datetime.timezone.utc).timestamp()
        rendered = em_logfmt.LocalTzFormatter("%(asctime)s").formatTime(
            _record(created))
        assert rendered.startswith("2026-01-15 07:00:00."), rendered
        assert rendered.endswith("-05:00"), rendered
    finally:
        if old is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_install_stamps_every_root_handler():
    handler = logging.StreamHandler()
    root = logging.getLogger()
    old_formatters = [h.formatter for h in root.handlers]
    root.addHandler(handler)
    try:
        em_logfmt.install("%(asctime)s %(message)s")
        assert isinstance(handler.formatter, em_logfmt.LocalTzFormatter)
    finally:
        root.removeHandler(handler)
        for h, formatter in zip(root.handlers, old_formatters):
            h.setFormatter(formatter)


# ── The startup summary ────────────────────────────────────────────────────

def test_startup_summary_states_levels_and_timezone():
    line = em_logfmt.startup_summary()
    assert "root=" in line, line
    assert re.search(r"UTC[+-]\d{2}:\d{2}", line), line


# ── The [TURN] suffix ──────────────────────────────────────────────────────

def test_turn_suffix_names_device_and_wake_bar():
    suffix = em_logfmt.turn_suffix(
        "G090L90971670C73",
        {"model": "hey_lana", "threshold": 0.2, "noise_floor": 0.01,
         "score": 0.4},
    )
    assert suffix.startswith(" device=G090L90971670C73"), suffix
    assert "wake_model=hey_lana" in suffix
    assert "wake_threshold=0.2" in suffix
    assert "noise_floor=0.01" in suffix
    # The score already rides in the trigger; repeating it here would
    # give one fact two spellings to disagree.
    assert "score" not in suffix


def test_turn_suffix_is_empty_when_there_is_nothing_to_add():
    assert em_logfmt.turn_suffix() == ""
    assert em_logfmt.turn_suffix("", None) == ""
    assert em_logfmt.turn_suffix("", {}) == ""


def test_turn_suffix_omits_absent_wake_fields():
    suffix = em_logfmt.turn_suffix("dev1", {"model": "hey_lana"})
    assert suffix == " device=dev1 wake_model=hey_lana"


def test_emit_appends_device_and_threshold(caplog):
    trace = em_turn_record.TurnTrace(
        trigger="wakeword(0.400)", t0=time.monotonic(),
        device_id="G090L90971670C73",
        wake_info={"model": "hey_lana", "threshold": 0.2,
                   "noise_floor": 0.01},
    )
    trace.outcome = "ok"
    with caplog.at_level(logging.INFO, logger="echomuse.esphome"):
        trace.emit()
    line = caplog.records[-1].getMessage()
    assert line.startswith("[TURN] trigger=wakeword(0.400) outcome=ok")
    assert "device=G090L90971670C73" in line
    assert "wake_model=hey_lana" in line
    assert "wake_threshold=0.2" in line


def test_emit_without_device_or_wake_is_the_upstream_line(caplog):
    trace = em_turn_record.TurnTrace(
        trigger="button", t0=time.monotonic())
    trace.outcome = "ok"
    with caplog.at_level(logging.INFO, logger="echomuse.esphome"):
        trace.emit()
    line = caplog.records[-1].getMessage()
    assert "device=" not in line
    assert "wake_" not in line
    assert line.endswith("fetch=—")
