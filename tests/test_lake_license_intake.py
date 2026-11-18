import hashlib
import json
import shutil
from pathlib import Path

import pytest

from tzgate.lake_license_intake import load_license_intake, preview_single_region, run_license_sale
from tools.fixture_data import FIXTURES

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "lake_license.yaml"
INTAKE = ROOT / "fixtures" / "lake_license" / "intake.json"

def _load(run_id="license-run"):
    return load_license_intake(CONTRACT, ROOT, INTAKE, run_id)

def _workspace(tmp_path):
    root = tmp_path / "ws"
    shutil.copytree(ROOT / "contracts", root / "contracts")
    shutil.copytree(ROOT / "fixtures", root / "fixtures")
    return root, root / "contracts" / "lake_license.yaml", root / "fixtures" / "lake_license" / "intake.json"

def _csv(root, region):
    return root / "fixtures" / "lake" / f"license_{region}" / "dt=2024-07-01" / "part-0001.csv"

def _mutate_intake(intake, mutate):
    data = json.loads(intake.read_text(encoding="utf-8"))
    mutate(data)
    intake.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def _repin(root, intake, region):
    raw = _csv(root, region).read_bytes()
    pin = {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
    _mutate_intake(intake, lambda data: data["files"].update({region: pin}))

def _synthetic(rows, bids, contracts=("row_weighted",)):
    regions = [{"region_id": r, "dataset": f"ds-{r}", "owner": f"owner-{r}", "row_count": n,
                "file": f"{r}/part.csv", "sha256": f"sha-{r}", "size_bytes": 1} for r, n in rows]
    return {"run_id": "synthetic", "contract_sha256": "c", "rulebook_sha256": "rb",
            "regions": regions, "bids": bids, "contracts": list(contracts)}

def test_verified_regions_and_unsafe_single_region_quote():
    data = _load()
    assert [(r["region_id"], r["row_count"]) for r in data["regions"]] == [("ca", 20), ("eu", 70), ("us", 160)]
    assert all(r["sha256"] and r["size_bytes"] > 0 for r in data["regions"])
    preview = preview_single_region(data)
    assert [(r["region_id"], r["bid_id"]) for r in preview["quotes"]] == [("ca", "b6"), ("eu", "b7"), ("us", "b4")]
    assert preview["quoted_total_cents"] == 2600
    assert [r["licensee"] for r in preview["quotes"]] == ["lic-1", "lic-1", "lic-4"]
    bids = {r["bid_id"]: r for r in data["bids"]}
    assert bids["b6"]["licensee"] == bids["b7"]["licensee"] == "lic-1"
    assert preview["settlement_computed"] is False
    # The 2600 quote is illegal: lic-1 would collect two mutually exclusive
    # alternatives, and every real settlement stays below it.
    for settlement in run_license_sale(data)["settlements"].values():
        winners = settlement["winners"]
        assert len({w["licensee"] for w in winners}) == len(winners)
        assert settlement["total_cents"] < 2600

def test_row_weighted_settlement_2500():
    data = _load()
    sale = run_license_sale(data)
    assert (sale["run_id"], sale["contract_sha256"], sale["rulebook_sha256"]) == (
        "license-run", data["contract_sha256"], data["rulebook_sha256"])
    assert {r["region_id"]: r["owner"] for r in sale["catalog"]} == {
        "ca": "owner-ca", "eu": "owner-eu", "us": "owner-us"}
    s = sale["settlements"]["row_weighted"]
    assert [w["bid_id"] for w in s["winners"]] == ["b1", "b4"]
    assert s["total_cents"] == 2500
    winners = {w["bid_id"]: w for w in s["winners"]}
    assert winners["b1"]["licensee"] == "lic-1" and winners["b1"]["regions"] == ["ca", "eu"]
    assert winners["b1"]["payouts"] == {"owner-ca": 444, "owner-eu": 1556}
    assert winners["b4"]["payouts"] == {"owner-us": 500}
    assert s["owner_totals"] == {"owner-ca": 444, "owner-eu": 1556, "owner-us": 500}
    assert s["unsold_regions"] == []
    assert s["conservation"] == {"per_bid": True, "batch": True, "collected_cents": 2500, "paid_cents": 2500}
    # Same-value rival b2+b7+b4 loses on winner count; the search is exhaustive.
    runners = {tuple(r["bid_ids"]): r for r in s["evidence"]["runners_up"]}
    assert runners[("b2", "b4", "b7")]["total_cents"] == 2500
    assert runners[("b2", "b4", "b7")]["lost_because"] == "more_winners"
    assert s["evidence"]["subsets_evaluated"] == 2**6 - 1

def test_standalone_floor_settlement_2100():
    sale = run_license_sale(_load())
    s = sale["settlements"]["standalone_floor"]
    assert s["floors"] == {"ca": 1600, "eu": 500, "us": 500}
    assert [w["bid_id"] for w in s["winners"]] == ["b4", "b6"]
    assert s["total_cents"] == 2100
    assert s["unsold_regions"] == ["eu"]
    winners = {w["bid_id"]: w for w in s["winners"]}
    assert winners["b6"]["payouts"] == {"owner-ca": 1600}
    assert winners["b4"]["payouts"] == {"owner-us": 500}
    assert s["owner_totals"] == {"owner-ca": 1600, "owner-eu": 0, "owner-us": 500}
    assert s["conservation"]["per_bid"] and s["conservation"]["batch"]
    unsold = {u["bid_id"]: u for u in s["unsold_bids"]}
    assert unsold["b1"]["reason"] == "below_floor_sum"
    assert (unsold["b1"]["value_cents"], unsold["b1"]["floor_sum_cents"]) == (2000, 2100)
    assert unsold["b2"]["reason"] == unsold["b3"]["reason"] == "below_floor_sum"
    assert unsold["b7"]["reason"] == "outbid" and unsold["b7"]["blocked_by"] == ["b6"]
    # Floors are eligibility, not a payment mode: b1 wins under row_weighted.
    assert "b1" in {w["bid_id"] for w in sale["settlements"]["row_weighted"]["winners"]}
    assert s["evidence"]["subsets_evaluated"] == 2**3 - 1

def test_same_licensee_alternatives_and_combo_competition():
    bids = [{"bid_id": "x1", "licensee": "lic-a", "regions": ["ca"], "value_cents": 120},
            {"bid_id": "x2", "licensee": "lic-a", "regions": ["eu"], "value_cents": 120},
            {"bid_id": "x3", "licensee": "lic-b", "regions": ["ca", "eu"], "value_cents": 200}]
    s = run_license_sale(_synthetic([("ca", 10), ("eu", 10)], bids))["settlements"]["row_weighted"]
    assert [w["bid_id"] for w in s["winners"]] == ["x3"]  # x1+x2=240 is mutex
    bids = [{"bid_id": "c1", "licensee": "lic-a", "regions": ["ca", "eu"], "value_cents": 300},
            {"bid_id": "s1", "licensee": "lic-b", "regions": ["ca"], "value_cents": 160},
            {"bid_id": "s2", "licensee": "lic-c", "regions": ["eu"], "value_cents": 160}]
    s = run_license_sale(_synthetic([("ca", 5), ("eu", 5)], bids))["settlements"]["row_weighted"]
    assert [w["bid_id"] for w in s["winners"]] == ["s1", "s2"]
    tied = [b if b["bid_id"] == "c1" else dict(b, value_cents=150) for b in bids]
    s = run_license_sale(_synthetic([("ca", 5), ("eu", 5)], tied))["settlements"]["row_weighted"]
    assert [w["bid_id"] for w in s["winners"]] == ["c1"]  # 300 == 300, fewer winners

def test_row_weighted_remainder_and_floor_excess_splits():
    bids = [{"bid_id": "c1", "licensee": "lic-a", "regions": ["r1", "r2", "r3"], "value_cents": 4}]
    s = run_license_sale(_synthetic([("r1", 1), ("r2", 1), ("r3", 1)], bids))["settlements"]["row_weighted"]
    assert s["winners"][0]["payouts"] == {"owner-r1": 2, "owner-r2": 1, "owner-r3": 1}
    bids = [{"bid_id": "f1", "licensee": "lic-x", "regions": ["ca"], "value_cents": 100},
            {"bid_id": "f2", "licensee": "lic-y", "regions": ["eu"], "value_cents": 50},
            {"bid_id": "cb", "licensee": "lic-z", "regions": ["ca", "eu"], "value_cents": 201}]
    data = _synthetic([("ca", 3), ("eu", 9)], bids, contracts=("standalone_floor",))
    s = run_license_sale(data)["settlements"]["standalone_floor"]
    assert s["floors"] == {"ca": 100, "eu": 50}
    assert s["winners"][0]["payouts"] == {"owner-ca": 126, "owner-eu": 75}
    assert s["conservation"]["per_bid"]

def test_missing_or_mispartitioned_file_blocks_the_sale(tmp_path):
    root, contract, intake = _workspace(tmp_path)
    _csv(root, "us").unlink()
    with pytest.raises(ValueError, match="one clean file"):
        load_license_intake(contract, root, intake, "missing-file")
    root, contract, intake = _workspace(tmp_path / "second")
    moved = root / "fixtures/lake/license_ca/dt=2024-07-02"
    moved.mkdir()
    shutil.move(str(_csv(root, "ca")), moved / "part-0001.csv")
    with pytest.raises(ValueError, match="verify cleanly"):
        load_license_intake(contract, root, intake, "wrong-partition")

def test_file_version_and_row_count_drive_the_sale(tmp_path):
    root, contract, intake = _workspace(tmp_path)
    lines = _csv(root, "eu").read_text(encoding="utf-8").splitlines()
    body = lines[1:]
    body[0], body[1] = body[1], body[0]
    _csv(root, "eu").write_text("\n".join([lines[0], *body]) + "\n", encoding="utf-8")
    # Reordered rows are a different file version: the pinned sale must not close.
    with pytest.raises(ValueError, match="pinned verified version"):
        load_license_intake(contract, root, intake, "replayed-version")
    _repin(root, intake, "eu")
    data = load_license_intake(contract, root, intake, "replayed-version")
    s = run_license_sale(data)["settlements"]["row_weighted"]
    assert {w["bid_id"]: w for w in s["winners"]}["b1"]["payouts"] == {"owner-ca": 444, "owner-eu": 1556}
    extra = [f"eu-{i:03d},2024-07-01T12:00:00Z,eu" for i in range(70, 100)]
    _csv(root, "eu").write_text("\n".join(lines + extra) + "\n", encoding="utf-8")
    _repin(root, intake, "eu")
    data = load_license_intake(contract, root, intake, "row-growth")
    assert {r["region_id"]: r["row_count"] for r in data["regions"]}["eu"] == 100
    s = run_license_sale(data)["settlements"]["row_weighted"]
    assert {w["bid_id"]: w for w in s["winners"]}["b1"]["payouts"] == {"owner-ca": 333, "owner-eu": 1667}
    assert s["conservation"]["batch"]

@pytest.mark.parametrize("bid,match", [
    ({"bid_id": "b8", "licensee": "lic-9", "regions": ["moon"], "value_cents": 10}, "unknown regions"),
    ({"bid_id": "b4", "licensee": "lic-9", "regions": ["eu"], "value_cents": 10}, "duplicate bid_id"),
    ({"bid_id": "b8", "licensee": "lic-1", "regions": ["ca"], "value_cents": 1600}, "duplicates another offer"),
    ({"bid_id": "b8", "licensee": "lic-9", "regions": ["us"], "value_cents": -1}, "non-negative integer"),
])
def test_invalid_bids_are_rejected(tmp_path, bid, match):
    root, contract, intake = _workspace(tmp_path)
    _mutate_intake(intake, lambda data: data["bids"].append(bid))
    with pytest.raises(ValueError, match=match):
        load_license_intake(contract, root, intake, "invalid-bid")

def test_bid_order_permutation_keeps_results_stable(tmp_path):
    root, contract, intake = _workspace(tmp_path)
    _mutate_intake(intake, lambda data: data.update(bids=list(reversed(data["bids"]))))
    shuffled = run_license_sale(load_license_intake(contract, root, intake, "license-run"))
    assert shuffled == run_license_sale(_load())

def test_generator_declares_all_three_lake_regions():
    assert all(f"license_{r}" in FIXTURES for r in ("ca", "eu", "us"))
    assert [len(FIXTURES[f"license_{r}"]["files"][0][1]) for r in ("ca", "eu", "us")] == [20, 70, 160]
