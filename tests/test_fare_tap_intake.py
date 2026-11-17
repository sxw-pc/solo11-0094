from pathlib import Path

from tzgate.fare_tap_intake import load_fare_taps, preview_first_transfer


ROOT = Path(__file__).resolve().parents[1]


def test_verified_taps_and_preliminary_quote():
    data = load_fare_taps(ROOT / "contracts/fare_taps.yaml", ROOT,
                          ROOT / "fixtures/fare_taps/intake.json", "fare-run")
    assert len(data["taps"]) == 7
    assert len(data["files"]) == 1
    assert len(data["files"][0]["sha256"]) == 64
    assert data["contract_sha256"] and data["rulebook_sha256"]
    preview = preview_first_transfer(data)
    assert [(x["rider_id"], x["from_tap"], x["to_tap"], x["discount_cents"])
            for x in preview["quotes"]] == [
                ("H", "h1", "h2", 70), ("L1", "l1a", "l1b", 80),
                ("L2", "l2a", "l2b", 80)]
    assert preview["quoted_discount_cents"] == 230
    assert not preview["shared_budget_cleared"]
    assert data["terms"]["municipal_discount_budget_cents"] == 180
