"""Exclusive synthetic license sales over verified lake datasets.

The single-region preview can quote two mutually exclusive bids from one
licensee, so its total is not a settlement. ``run_license_sale`` is the real
market: exact winner determination under exclusivity, then owner settlement
per contract. All bids, owners and amounts are synthetic.
"""
from __future__ import annotations
import json
from itertools import combinations
from pathlib import Path
from .discovery import discover_dataset
from .engine import run_verification
from .io_utils import sha256_bytes

MAX_BIDS = 8
MAX_REGIONS = 4
SETTLEMENT_CONTRACTS = ("row_weighted", "standalone_floor")

def load_license_intake(contract_path: Path, root: Path, intake_path: Path, run_id: str) -> dict:
    data = json.loads(intake_path.read_text(encoding="utf-8"))
    result = run_verification(contract_path, root, run_id)
    if result.status != "pass":
        raise ValueError("license datasets must verify cleanly")
    by_name = {d.spec.name: d for d in result.datasets}
    region_map, owners, pins = data["regions"], data["owners"], data["files"]
    if set(by_name) != set(region_map.values()):
        raise ValueError("license region datasets disagree")
    if set(owners) != set(region_map) or set(pins) != set(region_map):
        raise ValueError("each region needs exactly one owner and one pinned file")
    if len(region_map) > MAX_REGIONS:
        raise ValueError(f"at most {MAX_REGIONS} regions fit the exact solver")
    regions, seen = [], set()
    for region, dataset in sorted(region_map.items()):
        run = by_name[dataset]
        refs = discover_dataset(run.spec).files
        if run.issue_count or len(refs) != 1:
            raise ValueError("region must have one clean file")
        if str(refs[0].path) in seen:
            raise ValueError("the same file cannot back two regions")
        seen.add(str(refs[0].path))
        sha256 = sha256_bytes(refs[0].path.read_bytes())
        size = refs[0].path.stat().st_size
        if pins[region] != {"sha256": sha256, "size_bytes": size}:
            raise ValueError(f"region {region} file is not the pinned verified version")
        regions.append({"region_id": region, "dataset": dataset, "owner": owners[region], "row_count": run.record_count,
                        "file": refs[0].label, "sha256": sha256, "size_bytes": size})
    bids = _validate_bids(data["bids"], {r["region_id"] for r in regions})
    contracts = data["contracts"]
    valid = isinstance(contracts, list) and contracts and len(set(contracts)) == len(contracts)
    if not valid or set(contracts) - set(SETTLEMENT_CONTRACTS):
        raise ValueError("intake must authorize distinct known settlement contracts")
    return {"run_id": run_id, "contract_sha256": result.contract.sha256,
            "rulebook_sha256": result.contract.rulebook_sha256, "regions": regions,
            "bids": bids, "contracts": list(contracts)}

def preview_single_region(data: dict) -> dict:
    """Local single-region price quote, not an exclusive license sale."""
    chosen = []
    for region in data["regions"]:
        options = [b for b in data["bids"] if b["regions"] == [region["region_id"]]]
        if options:
            bid = min(options, key=lambda b: (-b["value_cents"], b["bid_id"]))
            chosen.append({"region_id": region["region_id"], "bid_id": bid["bid_id"],
                           "licensee": bid["licensee"], "value_cents": bid["value_cents"]})
    return {"quotes": chosen, "quoted_total_cents": sum(r["value_cents"] for r in chosen),
            "settlement_computed": False}

def run_license_sale(data: dict) -> dict:
    """Exact exclusive sale plus owner settlement for each authorized contract."""
    rows = {r["region_id"]: r["row_count"] for r in data["regions"]}
    owners = {r["region_id"]: r["owner"] for r in data["regions"]}
    settlements = {}
    for name in data["contracts"]:
        if name not in SETTLEMENT_CONTRACTS:
            raise ValueError(f"unknown settlement contract: {name}")
        settlements[name] = _settle(name, data["bids"], rows, owners)
    return {"run_id": data["run_id"], "contract_sha256": data["contract_sha256"],
            "rulebook_sha256": data["rulebook_sha256"], "catalog": data["regions"],
            "settlements": settlements}

def _validate_bids(raw_bids, region_ids: set) -> list:
    if not isinstance(raw_bids, list) or not raw_bids:
        raise ValueError("intake must list at least one bid")
    if len(raw_bids) > MAX_BIDS:
        raise ValueError(f"at most {MAX_BIDS} bids fit the exact solver")
    bids, seen_ids, seen_alt = [], set(), set()
    for raw in raw_bids:
        bid_id = raw.get("bid_id") if isinstance(raw, dict) else None
        if not isinstance(bid_id, str) or not bid_id.strip():
            raise ValueError("bid_id must be a non-empty string")
        if bid_id in seen_ids:
            raise ValueError(f"duplicate bid_id: {bid_id}")
        seen_ids.add(bid_id)
        licensee, regions, value = raw.get("licensee"), raw.get("regions"), raw.get("value_cents")
        if not isinstance(licensee, str) or not licensee.strip():
            raise ValueError(f"bid {bid_id} needs a licensee")
        if not isinstance(regions, list) or not regions or len(set(regions)) != len(regions):
            raise ValueError(f"bid {bid_id} must cover distinct regions")
        unknown = sorted(set(regions) - region_ids)
        if unknown:
            raise ValueError(f"bid {bid_id} references unknown regions: {', '.join(unknown)}")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"bid {bid_id} value_cents must be a non-negative integer")
        alternative = (licensee, tuple(sorted(regions)), value)
        if alternative in seen_alt:
            raise ValueError(f"bid {bid_id} duplicates another offer from {licensee}")
        seen_alt.add(alternative)
        bids.append({"bid_id": bid_id, "licensee": licensee,
                     "regions": list(regions), "value_cents": value})
    return bids

def _settle(contract: str, bids: list, rows: dict, owners: dict) -> dict:
    floors = {region: 0 for region in rows}
    if contract == "standalone_floor":
        for bid in bids:
            if len(bid["regions"]) == 1:
                region = bid["regions"][0]
                floors[region] = max(floors[region], bid["value_cents"])
    eligible, unsold = [], []
    for bid in sorted(bids, key=lambda b: b["bid_id"]):
        floor_sum = sum(floors[r] for r in bid["regions"])
        if contract == "standalone_floor" and bid["value_cents"] < floor_sum:
            # Floors are an eligibility gate, not a post-hoc payment mode.
            unsold.append({"bid_id": bid["bid_id"], "reason": "below_floor_sum",
                           "value_cents": bid["value_cents"], "floor_sum_cents": floor_sum})
        else:
            eligible.append(bid)
    best_ids, evaluated, runners_up = _exact_allocation(eligible)
    winners, owner_totals = [], {owner: 0 for owner in owners.values()}
    for bid in eligible:
        if bid["bid_id"] not in best_ids:
            continue
        regions = sorted(bid["regions"])
        weighted = contract == "row_weighted"
        pairs = [(r, rows[r] if weighted else floors[r]) for r in regions]
        split = (_split_row_weighted if weighted else _split_floor_excess)(bid["value_cents"], pairs)
        payouts = {owners[r]: cents for r, cents in split.items()}
        for owner, cents in payouts.items():
            owner_totals[owner] += cents
        winners.append({"bid_id": bid["bid_id"], "licensee": bid["licensee"], "regions": regions,
                        "value_cents": bid["value_cents"], "payouts": payouts})
    for bid in eligible:
        if bid["bid_id"] not in best_ids:
            blocked = [w["bid_id"] for w in winners
                       if w["licensee"] == bid["licensee"] or set(w["regions"]) & set(bid["regions"])]
            unsold.append({"bid_id": bid["bid_id"], "reason": "outbid", "blocked_by": blocked})
    unsold.sort(key=lambda u: u["bid_id"])
    total, paid = sum(w["value_cents"] for w in winners), sum(owner_totals.values())
    result = {"contract": contract, "winners": winners, "unsold_bids": unsold,
              "unsold_regions": sorted(set(rows) - {r for w in winners for r in w["regions"]}),
              "owner_totals": dict(sorted(owner_totals.items())), "total_cents": total,
              "conservation": {"per_bid": all(sum(w["payouts"].values()) == w["value_cents"] for w in winners),
                               "batch": paid == total, "collected_cents": total, "paid_cents": paid},
              "evidence": {"method": "exhaustive", "eligible_bids": len(eligible), "subsets_evaluated": evaluated,
                           "selection_rule": "max total_cents, then fewest winners, then lowest sorted bid_id list",
                           "best_total_cents": total, "winner_count": len(winners), "runners_up": runners_up}}
    if contract == "standalone_floor":
        result["floors"] = {r: floors[r] for r in sorted(floors)}
    return result

def _exact_allocation(bids: list) -> tuple:
    ordered = sorted(bids, key=lambda b: b["bid_id"])
    feasible, evaluated = [], 0
    for size in range(1, len(ordered) + 1):
        for combo in combinations(ordered, size):
            evaluated += 1
            regions = [r for b in combo for r in b["regions"]]
            licensees = [b["licensee"] for b in combo]
            if len(regions) == len(set(regions)) and len(licensees) == len(set(licensees)):
                feasible.append((sum(b["value_cents"] for b in combo), size,
                                 tuple(b["bid_id"] for b in combo)))
    if not feasible:
        return (), evaluated, []
    best = min(feasible, key=lambda c: (-c[0], c[1], c[2]))
    runners = sorted((c for c in feasible if c[2] != best[2]),
                     key=lambda c: (-c[0], c[1], c[2]))[:3]
    return best[2], evaluated, [
        {"bid_ids": list(ids), "total_cents": total, "winner_count": count,
         "lost_because": "lower_total" if total < best[0] else
         "more_winners" if count > best[1] else "bid_id_order"}
        for total, count, ids in runners]

def _split_row_weighted(value_cents: int, rows: list) -> dict:
    """Leftover cents follow the largest fractional share, ties by region_id."""
    total_rows = sum(count for _, count in rows)
    if total_rows <= 0:  # zero verified rows: fall back to an even split
        share, leftover = divmod(value_cents, len(rows))
        return {r: share + (1 if i < leftover else 0) for i, (r, _) in enumerate(rows)}
    shares = [[r, *divmod(value_cents * count, total_rows)] for r, count in rows]
    leftover = value_cents - sum(share[1] for share in shares)
    for share in sorted(shares, key=lambda s: (-s[2], s[0]))[:leftover]:
        share[1] += 1
    return {r: cents for r, cents, _ in shares}

def _split_floor_excess(value_cents: int, floors: list) -> dict:
    """Owners keep their floor; excess is shared evenly, leftovers by region_id."""
    share, leftover = divmod(value_cents - sum(f for _, f in floors), len(floors))
    return {r: f + share + (1 if i < leftover else 0) for i, (r, f) in enumerate(floors)}
