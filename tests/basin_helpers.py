"""Shared offline workspace builders for the basin attribution tests."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from tzgate.basin import attribute_basin, load_basin_intake
from tzgate.io_utils import canonical_json_text

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "basin_attribution.yaml"
INTAKE = ROOT / "fixtures" / "basin_attribution" / "intake.json"
RULEBOOK_TEXT = (ROOT / "contracts" / "timezone_rules.yaml").read_text("utf-8")
CONTRACT_TEXT = CONTRACT.read_text("utf-8")
HEADER = ("reading_id", "event_time", "source_id", "sensor_id", "load_units")
SENSORS = {
    "north": {"coefficients": [1, 1, 0], "bias_coefficient": 1},
    "south": {"coefficients": [0, 1, 1], "bias_coefficient": -1},
    "total": {"coefficients": [1, 1, 1], "bias_coefficient": 0},
}
CAPS = {"A": 8, "B": 7, "C": 6}
FINE = "fine_only_if_same_source_violates_in_every_world"
ORDER = "order_if_some_source_violates_in_every_world"
T = "2024-07-01T10:00:00Z"
T2 = "2024-07-01T11:00:00Z"


def write_csv(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows(rows)
    path.write_text(buf.getvalue(), "utf-8")


def rules(*, sensors=None, biases=(-1, 0, 1), bounds=(0, 10), caps=None,
          constant=True, dischargers=("A", "B", "C")) -> dict:
    caps = caps or {name: CAPS[name] for name in dischargers}
    return {
        "dataset": "basin_monitors",
        "dischargers": list(dischargers),
        "sensors": sensors or SENSORS,
        "common_bias_values": list(biases),
        "bias_constant_across_slots": constant,
        "load_bounds": list(bounds),
        "contracts": {
            "individual_fine": {"daily_caps": dict(caps), "decision": FINE},
            "collective_remediation": {"daily_caps": dict(caps), "decision": ORDER},
        },
    }


def rows(readings: dict[str, dict[str, int]]) -> list[tuple]:
    out, index = [], 0
    for slot in sorted(readings):
        for sensor in sorted(readings[slot]):
            out.append((f"r-{index}", slot, "basin", sensor,
                        str(readings[slot][sensor])))
            index += 1
    return out


def workspace(tmp: Path, data: list[tuple], spec: dict):
    root = tmp / "root"
    write_csv(
        root / "fixtures/lake/basin_monitors/dt=2024-07-01/part-0001.csv", data
    )
    (root / "contracts").mkdir(parents=True)
    (root / "contracts/timezone_rules.yaml").write_text(RULEBOOK_TEXT, "utf-8")
    contract = root / "contracts/basin_attribution.yaml"
    contract.write_text(CONTRACT_TEXT, "utf-8")
    intake = root / "intake.json"
    intake.write_text(canonical_json_text(spec).rstrip("\n"), "utf-8")
    return root, contract, intake


def attribute(tmp: Path, data: list[tuple], spec: dict | None = None, **kw):
    root, contract, intake = workspace(tmp, data, spec or rules())
    return attribute_basin(
        load_basin_intake(contract, root, intake, "test-run"), **kw
    )


def daily(world: dict, source: str) -> int:
    return sum(slot[source] for slot in world["loads"].values())
