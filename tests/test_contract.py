from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tzgate.contract import load_contract, validate_zone_references
from tzgate.errors import ContractError

RULEBOOK = """ version: 1 zones: UTC: base_offset: "+00:00" transitions: [] """

CONTRACT = """
version: 1
rulebook: rules.yaml
datasets:
  - name: sample
    root: lake
    partition: {pattern: "dt={date}", date_format: "%Y-%m-%d", basis: zone_local_date}
    time: {column: event_time, format: iso8601, semantics: utc_instant, zone: UTC}
"""

def _project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "project"
    (root / "contracts").mkdir(parents=True)
    (root / "lake" / "dt=2024-01-01").mkdir(parents=True)
    (root / "contracts" / "rules.yaml").write_text( textwrap.dedent(RULEBOOK), encoding="utf-8" )
    contract = root / "contracts" / "default.yaml"
    contract.write_text(textwrap.dedent(CONTRACT), encoding="utf-8")
    return root, contract

def test_default_contract(repo_root: Path):
    contract = load_contract(repo_root / "contracts" / "default.yaml", repo_root)
    assert contract.label == "contracts/default.yaml"
    assert contract.rulebook_label == "contracts/timezone_rules.yaml"
    assert len(contract.datasets) == 4
    names = [item.name for item in contract.datasets]
    assert names == ["orders_local", "events_utc", "sensor_offsets", "sessions_sydney"]
    assert contract.datasets[1].time.semantics == "utc_instant"
    assert contract.datasets[1].time.zone == "Europe/Berlin"
    assert len(contract.sha256) == 64
    assert len(contract.rulebook_sha256) == 64

def test_loads_minimal_contract(tmp_path: Path):
    root, contract_path = _project(tmp_path)
    contract = load_contract(contract_path, root)
    assert contract.datasets[0].root_path == (root / "lake").resolve()
    assert contract.datasets[0].time.required is True

@pytest.mark.parametrize( "replacement", [ "    root: /tmp/elsewhere\n", "    root: ../outside\n", ], )
def test_rejects_unsafe_roots(tmp_path: Path, replacement: str):
    root, contract_path = _project(tmp_path)
    text = textwrap.dedent(CONTRACT).replace("    root: lake\n", replacement)
    contract_path.write_text(text, encoding="utf-8")
    with pytest.raises(ContractError):
        load_contract(contract_path, root)

def test_rejects_unknown_checks_and_duplicates(tmp_path: Path):
    root, contract_path = _project(tmp_path)
    text = textwrap.dedent(CONTRACT) + "    checks:\n      invented: true\n"
    contract_path.write_text(text, encoding="utf-8")
    with pytest.raises(ContractError):
        load_contract(contract_path, root)

    duplicate = textwrap.dedent(CONTRACT) + textwrap.dedent(CONTRACT).split("datasets:", 1)[1]
    contract_path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(ContractError):
        load_contract(contract_path, root)

def test_unknown_zone_is_rejected_after_rulebook_load(tmp_path: Path):
    root, contract_path = _project(tmp_path)
    contract_path.write_text( textwrap.dedent(CONTRACT).replace("zone: UTC", "zone: Mars/Olympus"), encoding="utf-8", )
    contract = load_contract(contract_path, root)
    with pytest.raises(ContractError):
        validate_zone_references(contract, {"UTC"})

def test_contract_must_stay_inside_root(tmp_path: Path):
    root, contract_path = _project(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text(contract_path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ContractError):
        load_contract(outside, root)

def test_bad_version(tmp_path: Path):
    root, contract_path = _project(tmp_path)
    contract_path.write_text( contract_path.read_text(encoding="utf-8").replace("version: 1", "version: 2"), encoding="utf-8", )
    with pytest.raises(ContractError):
        load_contract(contract_path, root)

