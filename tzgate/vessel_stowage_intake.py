"""Verified cargo manifest intake, exact stowage planning and port actions."""
from __future__ import annotations

import csv
import json
from itertools import combinations, permutations
from pathlib import Path

from .discovery import discover_dataset
from .engine import run_verification
from .errors import OutputError
from .io_utils import atomic_write_text, canonical_json_text, sha256_bytes
from .ledger import write_stowage_ledger

# 合同上限：离线有界精确搜索的边界，超出即拒签。
MAX_CONTAINERS, MAX_STACKS, MAX_LAYERS, MAX_PORTS = 6, 3, 2, 2
STACK_RULE = "top_mass_not_greater_than_bottom_mass"
OBJECTIVE = "max_freight_cents_minus_rehandle_cost"
TIE_BREAK = "net_cents 高者优先；并列依次按倒箱少、承运箱多、箱号、舱位图、回装位字典序稳定裁决"


def load_stowage_intake(contract_path: Path, root: Path, intake_path: Path, run_id: str) -> dict:
    rules = json.loads(intake_path.read_text(encoding="utf-8"))
    if not isinstance(rules, dict): raise ValueError("intake rules must be a JSON object")
    result = run_verification(contract_path, root, run_id)
    if result.status != "pass": raise ValueError("cargo manifest must verify cleanly")
    by_name = {d.spec.name: d for d in result.datasets}
    if set(by_name) != {rules.get("dataset")}: raise ValueError("cargo dataset disagrees")
    dataset = by_name[rules.get("dataset")]
    refs = discover_dataset(dataset.spec).files
    if dataset.issue_count or len(refs) != 1: raise ValueError("one clean cargo file required")
    ref = refs[0]
    with ref.path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != dataset.record_count: raise ValueError("verified row count changed")
    try: intake = intake_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError: intake = intake_path.name
    return {"run_id": run_id, "contract": result.contract.label, "contract_sha256": result.contract.sha256,
            "rulebook": result.contract.rulebook_label, "rulebook_sha256": result.contract.rulebook_sha256,
            "file": ref.label, "file_sha256": sha256_bytes(ref.path.read_bytes()), "intake": intake,
            "intake_sha256": sha256_bytes(intake_path.read_bytes()), "containers": rows, "rules": rules}


def preview_gross_manifest(data: dict) -> dict:
    """Gross available cargo without a safe stowage or unload sequence."""
    return {"count": len(data["containers"]),
            "mass_tons": sum(int(row["mass_tons"]) for row in data["containers"]),
            "freight_cents": sum(int(row["freight_cents"]) for row in data["containers"])}


def _nonneg_int(value, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0: raise ValueError(f"{label}须为非负整数")
    return value


def _validate_rules(rules: dict) -> dict:
    stacks = rules.get("stacks")
    if not isinstance(stacks, dict) or not 1 <= len(stacks) <= MAX_STACKS:
        raise ValueError(f"堆数须在1..{MAX_STACKS}，拒签")
    for name, layers in stacks.items():
        if not (isinstance(name, str) and name.strip() and isinstance(layers, list)
                and 1 <= len(layers) <= MAX_LAYERS and len(set(layers)) == len(layers)):
            raise ValueError(f"堆{name}声明非法，拒签")
    ports = rules.get("port_order")
    ok = isinstance(ports, list) and 1 <= len(ports) <= MAX_PORTS and len(set(ports)) == len(ports)
    if not ok or not all(isinstance(p, str) and p for p in ports):
        raise ValueError(f"港序须为1..{MAX_PORTS}个不重复港口，拒签")
    if rules.get("stack_rule") != STACK_RULE or rules.get("objective") != OBJECTIVE:
        raise ValueError("不支持的堆叠规则或目标函数，拒签")
    contracts = rules.get("contracts")
    if not isinstance(contracts, dict) or not contracts: raise ValueError("缺少承运合同，拒签")
    parsed = {}
    for name, item in contracts.items():
        if not isinstance(item, dict): raise ValueError(f"合同{name}非法，拒签")
        parsed[name] = {"max_rehandles": _nonneg_int(item.get("max_rehandles_at_P1"), f"{name}.max_rehandles_at_P1"),
                        "rehandle_cost": _nonneg_int(item.get("rehandle_cost_cents"), f"{name}.rehandle_cost_cents")}
    return {"stacks": stacks, "port_order": list(ports), "contracts": parsed,
            "initial": _nonneg_int(rules.get("max_initial_mass_imbalance_tons"), "初航质量差上限"),
            "post": _nonneg_int(rules.get("max_post_P1_mass_imbalance_tons"), "离港质量差上限")}


def _parse_containers(rows: list, port_order: list) -> list:
    seen, out = set(), []
    for row in rows:
        cid = (row.get("container_id") or "").strip()
        if not cid or cid in seen: raise ValueError(f"箱号为空或重复:{cid or '?'}，拒签")
        seen.add(cid)
        port = (row.get("unload_port") or "").strip()
        if port not in port_order: raise ValueError(f"箱{cid}卸港{port or '空'}非法，拒签")
        try: mass, freight = int((row.get("mass_tons") or "").strip()), int((row.get("freight_cents") or "").strip())
        except ValueError as exc: raise ValueError(f"箱{cid}重量/运费非整数，拒签") from exc
        if mass <= 0 or freight < 0: raise ValueError(f"箱{cid}重量{mass}/运费{freight}非法，拒签")
        if not (row.get("source_id") or "").strip(): raise ValueError(f"箱{cid}来源缺失，拒签")
        out.append({"container_id": cid, "mass_tons": mass, "unload_port": port, "freight_cents": freight,
                    "source_id": row["source_id"].strip()})
    if len(out) > MAX_CONTAINERS: raise ValueError(f"箱数超过{MAX_CONTAINERS}上限，拒签")
    return sorted(out, key=lambda c: c["container_id"])


def _layout_kinds(layout: dict, containers: list, stacks: dict, s: str) -> set:
    occ = [i for i in range(len(stacks[s])) if (s, i) in layout]
    heavy = any(containers[layout[(s, i)]]["mass_tons"] > containers[layout[(s, i - 1)]]["mass_tons"] for i in occ[1:])
    return ({"舱位悬空"} if occ != list(range(len(occ))) else set()) | ({"顶层重于底层"} if heavy else set())


def _masses(layout: dict, containers: list, stacks: dict) -> dict:
    return {s: sum(containers[layout[(s, i)]]["mass_tons"] for i in range(len(stacks[s])) if (s, i) in layout)
            for s in sorted(stacks)}


def _imbalance(masses: dict) -> int:
    values = list(masses.values())
    return max(values) - min(values) if values else 0


def _evaluate(assignment: dict, containers: list, stacks: dict, ports: list, limits: dict, max_rehandles: int):
    """返回(违例集合, 可行细节)；违例集合非空则该配载不可行。"""
    kinds = set().union(*(_layout_kinds(assignment, containers, stacks, s) for s in stacks))
    if _imbalance(_masses(assignment, containers, stacks)) > limits["initial"]: kinds.add("初航左右质量差超限")
    rehandled = [b for (s, i), b in sorted(assignment.items()) if containers[b]["unload_port"] != ports[0]
                 and any(containers[assignment[(s, j)]]["unload_port"] == ports[0]
                         for j in range(i) if (s, j) in assignment)]
    if len(rehandled) > max_rehandles: kinds.add(f"{ports[0]}倒箱次数超限")
    restow, post_masses, why = _restow(assignment, rehandled, containers, stacks, ports, limits)
    if restow is None: kinds.add(why)
    detail = None if kinds else {"rehandled": rehandled, "restow": restow, "post_masses": post_masses}
    return kinds, detail


def _restow(assignment: dict, rehandled: list, containers: list, stacks: dict, ports: list, limits: dict):
    """为被倒箱选择离港后合法配载；返回(回装映射, 离港质量, 失败原因)。"""
    first = ports[0]
    unmoved = {slot: b for slot, b in assignment.items() if containers[b]["unload_port"] != first and b not in rehandled}
    free = [(s, i) for s in sorted(stacks) for i in range(len(stacks[s])) if (s, i) not in unmoved]
    best, saw_layout = None, False
    for combo in permutations(free, len(rehandled)):
        layout = {**unmoved, **dict(zip(combo, rehandled))}
        if set().union(*(_layout_kinds(layout, containers, stacks, s) for s in stacks)): continue
        saw_layout = True
        if _imbalance(_masses(layout, containers, stacks)) > limits["post"]: continue
        key = tuple(sorted((containers[b]["container_id"], slot) for slot, b in zip(combo, rehandled)))
        if best is None or key < best[0]: best = (key, dict(zip(rehandled, combo)), _masses(layout, containers, stacks))
    if best is not None: return best[1], best[2], None
    return None, None, (f"{first}离港后左右质量差超限" if saw_layout else "无合法回装舱位")


def _port_actions(assignment: dict, detail: dict, containers: list, stacks: dict, ports: list) -> list:
    state, actions = dict(assignment), []

    def record(port, action, box, slot):
        actions.append({"seq": len(actions) + 1, "port": port, "action": action,
                        "container": containers[box]["container_id"],
                        "stack": slot[0], "layer": stacks[slot[0]][slot[1]]})

    def sweep(port, lo):  # 卸出各堆 lo 层及以上可直接吊卸的本港箱
        for s in sorted(stacks):
            for i in range(len(stacks[s]) - 1, lo - 1, -1):
                b = state.get((s, i))
                if b is not None and containers[b]["unload_port"] == port:
                    record(port, "unload", b, (s, i)); del state[(s, i)]
    first = ports[0]
    sweep(first, 1)
    for s in sorted(stacks):  # 倒箱腾出底层后卸首港箱
        b = state.get((s, 0))
        if b is None or containers[b]["unload_port"] != first: continue
        for i in range(len(stacks[s]) - 1, 0, -1):
            if (s, i) in state: record(first, "lift_to_hold", state.pop((s, i)), (s, i))
        record(first, "unload", b, (s, 0)); del state[(s, 0)]
    for b, slot in sorted(detail["restow"].items(), key=lambda kv: (kv[1][1], kv[1][0], containers[kv[0]]["container_id"])):
        record(first, "restow", b, slot); state[slot] = b
    for port in ports[1:]:  # 后续港卸空剩余箱
        sweep(port, 0)
    return actions


def _check_actions(actions: list, assignment: dict, containers: list, stacks: dict) -> dict:
    """独立复核动作序列：不得卸错港、重复吊箱或让顶层悬空。"""
    by_id = {c["container_id"]: i for i, c in enumerate(containers)}
    layer_idx = {s: {n: i for i, n in enumerate(ls)} for s, ls in stacks.items()}
    state, hold, done, lifts, problems = dict(assignment), set(), set(), {}, []
    for a in actions:
        b, bad, (s, i) = by_id[a["container"]], problems.append, (a["stack"], layer_idx[a["stack"]][a["layer"]])
        lifts[b] = lifts.get(b, 0) + 1
        clear = all((s, j) not in state for j in range(i + 1, len(stacks[s])))
        if a["action"] in ("unload", "lift_to_hold"):
            if state.get((s, i)) != b or not clear: bad(f"悬空吊箱:{a['container']}")
            if a["action"] == "unload":
                if containers[b]["unload_port"] != a["port"]: bad(f"卸错港:{a['container']}")
                if b in done: bad(f"重复卸箱:{a['container']}")
                done.add(b)
            else:
                if b in hold or b in done: bad(f"重复吊箱:{a['container']}")
                hold.add(b)
            state.pop((s, i), None)
        else:  # restow
            below = state.get((s, i - 1)) if i else "keel"
            if b not in hold: bad(f"未吊离即回装:{a['container']}")
            if (s, i) in state: bad(f"回装占位:{a['container']}")
            elif below is None: bad(f"悬空回装:{a['container']}")
            elif below != "keel" and containers[b]["mass_tons"] > containers[below]["mass_tons"]:
                bad(f"回装压重:{a['container']}")
            state[(s, i)] = b; hold.discard(b)
    problems += [f"滞留船上:{containers[b]['container_id']}" for b in state.values()] + [f"滞留吊具:{containers[b]['container_id']}" for b in hold]
    return {"ok": not problems, "problems": problems, "boxes_unloaded": len(done),
            "max_lifts_per_box": max(lifts.values(), default=0)}


def _assemble(assignment: dict, detail: dict, containers: list, rules: dict, name: str) -> dict:
    stacks, ports, contract = rules["stacks"], rules["port_order"], rules["contracts"][name]
    actions = _port_actions(assignment, detail, containers, stacks, ports)
    initial = _masses(assignment, containers, stacks)
    freight = sum(containers[b]["freight_cents"] for b in assignment.values())
    cost = len(detail["rehandled"]) * contract["rehandle_cost"]
    return {"contract": name, "carried": sorted(containers[b]["container_id"] for b in assignment.values()),
            "slots": {s: {layer: (containers[assignment[(s, i)]]["container_id"] if (s, i) in assignment else None)
                          for i, layer in enumerate(layers)} for s, layers in sorted(stacks.items())},
            "initial_mass": {"per_stack": initial, "imbalance": _imbalance(initial), "limit": rules["initial"]},
            "post_departure_mass": {"port": ports[0], "per_stack": detail["post_masses"], "limit": rules["post"],
                                    "imbalance": _imbalance(detail["post_masses"])},
            "port_calls": [{"port": p, "actions": [a for a in actions if a["port"] == p]} for p in ports],
            "rehandles": len(detail["rehandled"]), "rehandle_limit": contract["max_rehandles"], "freight_cents": freight,
            "unit_rehandle_cost_cents": contract["rehandle_cost"], "rehandle_cost_cents": cost, "net_cents": freight - cost,
            "action_checks": _check_actions(actions, assignment, containers, stacks)}


def _plan_contract(containers: list, rules: dict, name: str) -> dict:
    stacks, ports = rules["stacks"], rules["port_order"]
    limits = {"initial": rules["initial"], "post": rules["post"]}
    contract, n = rules["contracts"][name], len(containers)
    slots = [(s, i) for s in sorted(stacks) for i in range(len(stacks[s]))]
    stats = {"subsets_evaluated": 0, "assignments_evaluated": 0, "feasible_assignments": 0}
    full = {"ids": [c["container_id"] for c in containers], "violation_counts": {},
            "assignments_evaluated": 0, "feasible_assignments": 0}
    best, best_net_with = None, {}
    for r in range(n + 1):
        for subset in combinations(range(n), r):
            stats["subsets_evaluated"] += 1
            for pick in permutations(slots, r):
                assignment = dict(zip(pick, subset))
                stats["assignments_evaluated"] += 1
                kinds, detail = _evaluate(assignment, containers, stacks, ports, limits, contract["max_rehandles"])
                if r == n:
                    full["assignments_evaluated"] += 1
                    full["feasible_assignments"] += not kinds
                    for kind in kinds: full["violation_counts"][kind] = full["violation_counts"].get(kind, 0) + 1
                if kinds: continue
                stats["feasible_assignments"] += 1
                rehandles = len(detail["rehandled"])
                net = sum(containers[b]["freight_cents"] for b in subset) - rehandles * contract["rehandle_cost"]
                carried = tuple(sorted(containers[b]["container_id"] for b in subset))
                slot_tuple = tuple(containers[assignment[slot]]["container_id"] if slot in assignment else "" for slot in slots)
                restow_tuple = tuple(sorted((containers[b]["container_id"], slot[0], slot[1]) for b, slot in detail["restow"].items()))
                key = (-net, rehandles, -r, carried, slot_tuple, restow_tuple)
                for b in subset:
                    best_net_with[containers[b]["container_id"]] = max(net, best_net_with.get(containers[b]["container_id"], -1))
                if best is None or key < best[0]: best = (key, assignment, detail)
    plan = _assemble(best[1], best[2], containers, rules, name)
    chosen = plan["net_cents"]

    def reason(best_net):
        if best_net is None: return "该箱在本合同限制下无可行配载（堆叠/配平/倒箱）"
        return (f"承运该箱的最优可行方案净收益{best_net}分，低于中选方案{chosen}分" if best_net < chosen
                else f"承运该箱的最优方案净收益{best_net}分与中选方案持平，按稳定裁决落选")

    plan["rejected"] = [{"container_id": c["container_id"], "reason": reason(best_net_with.get(c["container_id"]))}
                        for c in containers if c["container_id"] not in plan["carried"]]
    plan["evidence"] = {"objective": OBJECTIVE, "tie_break": TIE_BREAK,
                        "search": {**stats, "containers": n, "max_rehandles_at_first_port": contract["max_rehandles"]},
                        "full_manifest_set": {**full, "feasible": full["feasible_assignments"] > 0},
                        "action_checks": plan.pop("action_checks")}
    return plan


def build_stowage_decision(contract_path: Path, root: Path, intake_path: Path, run_id: str) -> dict:
    data = load_stowage_intake(contract_path, root, intake_path, run_id)
    rules = _validate_rules(data["rules"])
    containers = _parse_containers(data["containers"], rules["port_order"])
    prov = {"run_id": run_id, "contract_sha256": data["contract_sha256"],
            "rulebook_sha256": data["rulebook_sha256"], "file_sha256": data["file_sha256"]}
    plans = {name: {**_plan_contract(containers, rules, name), **prov} for name in sorted(rules["contracts"])}
    return {"schema_version": "stowage/1", "run_id": run_id,
            "contract": {"path": data["contract"], "sha256": data["contract_sha256"]},
            "rulebook": {"path": data["rulebook"], "sha256": data["rulebook_sha256"]},
            "manifest": {"dataset": data["rules"].get("dataset"), "file": data["file"], "sha256": data["file_sha256"], "records": len(containers)},
            "intake": {"path": data["intake"], "sha256": data["intake_sha256"]},
            "limits": {"max_containers": MAX_CONTAINERS, "max_stacks": MAX_STACKS, "max_layers": MAX_LAYERS, "max_ports": MAX_PORTS},
            "stacks": rules["stacks"], "port_order": rules["port_order"], "objective": OBJECTIVE,
            "containers": containers, "plans": plans}


def write_stowage_outputs(document: dict, out_dir: Path, root: Path):
    target, protected = Path(out_dir).resolve(), (Path(root).resolve() / "fixtures" / "lake").resolve()
    try: target.relative_to(protected)
    except ValueError: pass
    else: raise OutputError("--out must not be inside fixtures/lake")
    target.mkdir(parents=True, exist_ok=True)
    json_path, ledger_path = target / "stowage.json", target / "stowage.sqlite3"
    atomic_write_text(json_path, canonical_json_text(document))
    write_stowage_ledger(ledger_path, document)
    return json_path, ledger_path
