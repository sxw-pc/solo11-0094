"""Strict validation of intake declarations and verified monitor rows."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..errors import InputError

MAX_DISCHARGERS = 3
MAX_SLOTS = 4
MAX_SENSORS = 5
MAX_BIAS_VALUES = 3
LOAD_MIN = 0
LOAD_MAX = 10
WORLD_MATERIALIZATION_LIMIT = 1024

FINE_RULE = "fine_only_if_same_source_violates_in_every_world"
REMEDIATION_RULE = "order_if_some_source_violates_in_every_world"
KNOWN_RULES = frozenset({FINE_RULE, REMEDIATION_RULE})
RULE_KEYS = frozenset({"daily_caps", "decision"})
INTAKE_KEYS = frozenset({
    "dataset", "dischargers", "sensors", "common_bias_values",
    "bias_constant_across_slots", "load_bounds", "contracts",
})
_INTEGER_RE = re.compile(r"^[+-]?\d+$")
REQUIRED_COLUMNS = ("event_time", "sensor_id", "load_units")


class AttributionError(InputError, ValueError):
    """Attribution evidence is incomplete, inconsistent or infeasible."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_rules(rules: Any) -> dict[str, Any]:
    if not isinstance(rules, dict):
        raise AttributionError("intake rules must be a JSON object")
    unknown = sorted(set(rules) - INTAKE_KEYS)
    if unknown:
        raise AttributionError(f"unknown intake keys: {', '.join(unknown)}")
    for key in INTAKE_KEYS:
        if key not in rules:
            raise AttributionError(f"intake rules missing key: {key}")
    dischargers = _validate_dischargers(rules)
    sensors = _validate_sensors(rules, dischargers)
    _validate_bias(rules)
    bounds = _validate_bounds(rules)
    _validate_contracts(rules, dischargers)
    return rules


def _validate_dischargers(rules: dict[str, Any]) -> list[str]:
    dischargers = rules["dischargers"]
    if not isinstance(dischargers, list) or not dischargers:
        raise AttributionError("dischargers must be a non-empty list")
    if len(dischargers) > MAX_DISCHARGERS:
        raise AttributionError(f"at most {MAX_DISCHARGERS} dischargers are supported")
    if any(not isinstance(name, str) or not name.strip() for name in dischargers):
        raise AttributionError("discharger names must be non-empty strings")
    if len(set(dischargers)) != len(dischargers):
        raise AttributionError("discharger names must be unique")
    return dischargers


def _validate_sensors(rules: dict[str, Any], dischargers: list[str]) -> dict[str, Any]:
    sensors = rules["sensors"]
    if not isinstance(sensors, dict) or not sensors:
        raise AttributionError("sensors must be a non-empty object")
    if len(sensors) > MAX_SENSORS:
        raise AttributionError(f"at most {MAX_SENSORS} sensors are supported")
    for name, spec in sensors.items():
        if not isinstance(name, str) or not name.strip():
            raise AttributionError("sensor ids must be non-empty strings")
        if not isinstance(spec, dict):
            raise AttributionError(f"sensor {name} must be an object")
        if set(spec) != {"coefficients", "bias_coefficient"}:
            raise AttributionError(
                f"sensor {name} must declare coefficients and bias_coefficient"
            )
        coeff = spec["coefficients"]
        if not isinstance(coeff, list) or len(coeff) != len(dischargers):
            raise AttributionError(
                f"sensor {name} coefficients must align with the dischargers"
            )
        if any(not _is_int(value) for value in coeff):
            raise AttributionError(f"sensor {name} coefficients must be integers")
        if not _is_int(spec["bias_coefficient"]):
            raise AttributionError(f"sensor {name} bias_coefficient must be an integer")
    return sensors


def _validate_bias(rules: dict[str, Any]) -> None:
    bias_values = rules["common_bias_values"]
    if not isinstance(bias_values, list) or not bias_values:
        raise AttributionError("common_bias_values must be a non-empty list")
    if len(bias_values) > MAX_BIAS_VALUES:
        raise AttributionError(f"at most {MAX_BIAS_VALUES} calibration candidates")
    if any(not _is_int(value) for value in bias_values):
        raise AttributionError("calibration bias values must be integers")
    if len(set(bias_values)) != len(bias_values):
        raise AttributionError("calibration bias values must be distinct")
    if rules["bias_constant_across_slots"] is not True:
        raise AttributionError(
            "per-slot or per-sensor bias selection is prohibited: the "
            "calibration bias must be one shared value across all slots"
        )


def _validate_bounds(rules: dict[str, Any]) -> list[int]:
    bounds = rules["load_bounds"]
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise AttributionError("load_bounds must be a [min, max] pair")
    if any(not _is_int(value) for value in bounds):
        raise AttributionError("load_bounds must be integers")
    low, high = bounds
    if not (LOAD_MIN <= low <= high <= LOAD_MAX):
        raise AttributionError(
            f"load_bounds must satisfy {LOAD_MIN} <= min <= max <= {LOAD_MAX}"
        )
    return bounds


def _validate_contracts(rules: dict[str, Any], dischargers: list[str]) -> None:
    contracts = rules["contracts"]
    if not isinstance(contracts, dict) or not contracts:
        raise AttributionError("contracts must be a non-empty object")
    for name, spec in contracts.items():
        if not isinstance(spec, dict) or set(spec) != RULE_KEYS:
            raise AttributionError(
                f"contract {name} must declare daily_caps and decision"
            )
        caps = spec["daily_caps"]
        if not isinstance(caps, dict) or set(caps) != set(dischargers):
            raise AttributionError(
                f"contract {name} daily_caps must cover every discharger"
            )
        if any(not _is_int(value) or value < 0 for value in caps.values()):
            raise AttributionError(
                f"contract {name} caps must be non-negative integers"
            )
        if spec["decision"] not in KNOWN_RULES:
            raise AttributionError(f"contract {name} has an unknown decision rule")


def build_slots(rows: list[dict[str, str]], rules: dict[str, Any]) -> list[dict[str, Any]]:
    if not rows:
        raise AttributionError("verified basin file contains no readings")
    if len({row["event_time"] for row in rows}) > MAX_SLOTS:
        raise AttributionError(f"at most {MAX_SLOTS} monitoring slots are supported")
    grouped: dict[str, dict[str, int]] = {}
    for row in rows:
        slot = row["event_time"].strip()
        sensor = row["sensor_id"].strip()
        if sensor not in rules["sensors"]:
            raise AttributionError(f"reading from undeclared sensor: {sensor}")
        raw = row["load_units"].strip()
        if not _INTEGER_RE.fullmatch(raw):
            raise AttributionError(f"load_units must be an integer, got {raw!r}")
        existing = grouped.setdefault(slot, {})
        if sensor in existing:
            raise AttributionError(
                f"duplicate reading for slot {slot} and sensor {sensor}"
            )
        existing[sensor] = int(raw)
    declared = set(rules["sensors"])
    slots: list[dict[str, Any]] = []
    for slot in sorted(grouped):
        readings = grouped[slot]
        missing = sorted(declared - set(readings))
        if missing:
            raise AttributionError(
                f"slot {slot} is missing sensor readings: {', '.join(missing)}"
            )
        slots.append({
            "event_time": slot,
            "utc_date": _utc_date(slot),
            "readings": {name: readings[name] for name in sorted(declared)},
        })
    return slots


def _utc_date(value: str) -> str:
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AttributionError(f"slot timestamp is not ISO-8601: {value!r}") from exc
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise AttributionError(f"monitor timestamps must be UTC instants: {value!r}")
    return moment.astimezone(timezone.utc).date().isoformat()
