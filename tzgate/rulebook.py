"""Deterministic timezone rulebook: declared offsets and DST transitions.

No host tzdata is consulted. A zone is a base offset plus a strictly
increasing list of UTC transition instants. That is enough to classify local
wall times as normal, nonexistent or ambiguous and to map any UTC instant to
the local calendar date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from .errors import RulebookError
from .io_utils import sha256_bytes
from .offsets import OffsetSyntaxError, format_offset, parse_offset


@dataclass(frozen=True)
class Transition:
    at_utc: datetime
    offset: timedelta


@dataclass(frozen=True)
class Candidate:
    offset: timedelta
    instant_utc: datetime


@dataclass(frozen=True)
class LocalResolution:
    kind: str
    wall: datetime
    candidates: tuple[Candidate, ...]
    window_start: datetime | None = None
    window_end: datetime | None = None


@dataclass(frozen=True)
class ZoneRules:
    name: str
    base_offset: timedelta
    transitions: tuple[Transition, ...] = ()

    @property
    def offset_strings(self) -> tuple[str, ...]:
        values = {format_offset(self.base_offset)}
        values.update(format_offset(item.offset) for item in self.transitions)
        return tuple(sorted(values))

    def offset_at_utc(self, instant: datetime) -> timedelta:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("instant must be timezone-aware")
        moment = instant.astimezone(timezone.utc)
        current = self.base_offset
        for transition in self.transitions:
            if moment < transition.at_utc:
                break
            current = transition.offset
        return current

    def resolve_local(self, wall: datetime) -> LocalResolution:
        if wall.tzinfo is not None:
            wall = wall.replace(tzinfo=None)
        probe = wall.replace(tzinfo=timezone.utc)
        candidates: dict[datetime, Candidate] = {}
        offsets = {self.base_offset}
        offsets.update(item.offset for item in self.transitions)
        for offset in offsets:
            instant = (probe - offset).astimezone(timezone.utc)
            if self.offset_at_utc(instant) == offset:
                candidates[instant] = Candidate(offset=offset, instant_utc=instant)
        ordered = tuple(candidates[key] for key in sorted(candidates))
        window_start, window_end = self._window_for(wall)
        if not ordered:
            kind = "gap"
        elif len(ordered) == 1:
            kind = "normal"
        else:
            kind = "ambiguous"
        return LocalResolution(
            kind=kind,
            wall=wall,
            candidates=ordered,
            window_start=window_start,
            window_end=window_end,
        )

    def _window_for(self, wall: datetime) -> tuple[datetime | None, datetime | None]:
        current = self.base_offset
        for transition in self.transitions:
            before = (transition.at_utc + current).replace(tzinfo=None)
            after = (transition.at_utc + transition.offset).replace(tzinfo=None)
            if transition.offset > current and before <= wall < after:
                return before, after
            if transition.offset < current and after <= wall < before:
                return after, before
            current = transition.offset
        return None, None

    def local_date_for_instant(self, instant: datetime):
        local = instant.astimezone(timezone.utc) + self.offset_at_utc(instant)
        return local.date()


@dataclass(frozen=True)
class Rulebook:
    zones: Mapping[str, ZoneRules]
    sha256: str


def load_rulebook(path: Path, expected_sha256: str) -> Rulebook:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RulebookError(f"cannot read rulebook {path.name}: {exc}") from exc
    digest = sha256_bytes(data)
    if digest != expected_sha256:
        raise RulebookError("rulebook changed after the contract was loaded")
    try:
        raw = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise RulebookError(f"rulebook is not valid UTF-8 YAML: {exc}") from exc
    return _parse_rulebook(raw, digest)


def _parse_rulebook(raw: Any, digest: str) -> Rulebook:
    root = _mapping(raw, "rulebook")
    _only_keys(root, {"version", "zones", "description"}, "rulebook")
    if root.get("version") != 1:
        raise RulebookError("rulebook version must be 1")
    zones_raw = _mapping(root.get("zones"), "rulebook.zones")
    if not zones_raw:
        raise RulebookError("rulebook declares no zones")
    zones: dict[str, ZoneRules] = {}
    for name, zone_raw in zones_raw.items():
        if not isinstance(name, str) or not name.strip():
            raise RulebookError("zone names must be non-empty strings")
        zones[name] = _parse_zone(name, zone_raw)
    return Rulebook(zones=zones, sha256=digest)


def _parse_zone(name: str, raw: Any) -> ZoneRules:
    zone = _mapping(raw, f"zone {name}")
    _only_keys(zone, {"base_offset", "transitions", "description"}, f"zone {name}")
    base = _offset(zone.get("base_offset"), f"zone {name}.base_offset")
    transitions_raw = zone.get("transitions", [])
    if transitions_raw is None:
        transitions_raw = []
    if not isinstance(transitions_raw, list):
        raise RulebookError(f"zone {name}.transitions must be a list")
    transitions: list[Transition] = []
    previous: datetime | None = None
    current = base
    for index, item in enumerate(transitions_raw):
        label = f"zone {name}.transitions[{index}]"
        transition_raw = _mapping(item, label)
        _only_keys(transition_raw, {"at_utc", "offset", "description"}, label)
        at_utc = _utc(transition_raw.get("at_utc"), f"{label}.at_utc")
        offset = _offset(transition_raw.get("offset"), f"{label}.offset")
        if previous is not None and at_utc <= previous:
            raise RulebookError(f"{label}.at_utc must be strictly increasing")
        if offset == current:
            raise RulebookError(f"{label}.offset does not change the zone offset")
        transitions.append(Transition(at_utc=at_utc, offset=offset))
        previous = at_utc
        current = offset
    return ZoneRules(name=name, base_offset=base, transitions=tuple(transitions))


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RulebookError(f"{label} must be a mapping")
    return value


def _only_keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RulebookError(f"{label} has unknown keys: {', '.join(unknown)}")


def _offset(value: Any, label: str) -> timedelta:
    if not isinstance(value, str):
        raise RulebookError(f"{label} must be a quoted UTC offset")
    try:
        return parse_offset(value)
    except OffsetSyntaxError as exc:
        raise RulebookError(f"{label}: {exc}") from exc


def _utc(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise RulebookError(f"{label} is not ISO-8601: {value!r}") from exc
    else:
        raise RulebookError(f"{label} must be an ISO-8601 UTC timestamp")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise RulebookError(f"{label} must include Z or an explicit offset")
    return moment.astimezone(timezone.utc)
