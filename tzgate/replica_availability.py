"""Two-phase replica service planning over verified lake partitions.

engine.py proves the partition files and their identity; this module signs
- or refuses - service contracts on top of that verified set.  Pre-reveal
at most one node may be hardened (harden_cost) and then survives every
scenario; post-reveal at most one broken node per scenario may be repaired
(repair_cost).  Repair never rewrites the initial outage.  Bounds: 8
nodes, 5 regions, 4 scenarios, 1 harden, 1 repair.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .discovery import discover_dataset
from .engine import run_verification
from .io_utils import sha256_bytes

MAX_NODES, MAX_REGIONS, MAX_SCENARIOS = 8, 5, 4
MAX_HARDENED_NODES = MAX_REPAIRS_PER_SCENARIO = 1


def _is_cost(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0)


def _known(references: list, known: set, label: str) -> None:
    unknown = sorted(set(references) - known)
    if unknown:
        raise ValueError(f'{label} references unknown node(s): {", ".join(unknown)}')


def validate_replica_data(data: dict) -> None:
    """Reject malformed intake before any service arithmetic happens."""
    if not isinstance(data, dict):
        raise ValueError('replica intake must be a mapping')
    if not isinstance(data.get('dataset'), str) or not data['dataset'].strip():
        raise ValueError('replica intake must name a dataset')
    ids: dict[str, set] = {}
    kinds = (('node', 'node_id', MAX_NODES), ('region', 'region_id', MAX_REGIONS), ('scenario', 'scenario_id', MAX_SCENARIOS))
    for kind, key, limit in kinds:
        items = data.get(f'{kind}s')
        if not isinstance(items, list) or not 0 < len(items) <= limit:
            raise ValueError(f'{kind}s must be a list of 1..{limit}')
        seen: list[str] = []
        for item in items:
            item_id = item.get(key) if isinstance(item, dict) else None
            if not isinstance(item_id, str) or not item_id.strip():
                raise ValueError(f'each {kind} needs a non-empty {key}')
            if item_id in seen:
                raise ValueError(f'duplicate {key}: {item_id}')
            seen.append(item_id)
        ids[kind] = set(seen)
    for node in data['nodes']:
        for field in ('harden_cost', 'repair_cost'):
            if not _is_cost(node.get(field)):
                raise ValueError(f"node {node['node_id']} has invalid {field}")
    for region in data['regions']:
        replicas = region.get('replicas')
        if not isinstance(region.get('file'), str) or not region['file'].strip():
            raise ValueError(f"region {region['region_id']} needs a non-empty file")
        if not isinstance(replicas, list) or not replicas or len(set(replicas)) != len(replicas):
            raise ValueError(f"region {region['region_id']} replicas must be non-empty and unique")
        _known(replicas, ids['node'], f"region {region['region_id']}")
    for scenario in data['scenarios']:
        failed = scenario.get('failed_nodes')
        if not isinstance(failed, list) or len(set(failed)) != len(failed):
            raise ValueError(f"scenario {scenario['scenario_id']} failed_nodes must be unique")
        _known(failed, ids['node'], f"scenario {scenario['scenario_id']}")
    contracts = data.get('contracts')
    if not isinstance(contracts, dict) or not contracts:
        raise ValueError('contracts must be a non-empty mapping')
    for contract_id, spec in contracts.items():
        if not isinstance(spec, dict) or not {'temporary_outage_region', 'compensation'} <= spec.keys():
            raise ValueError(f'contract {contract_id} must declare both obligations')
        temporary = spec['temporary_outage_region']
        if temporary is not None and temporary not in ids['region']:
            raise ValueError(f'contract {contract_id} names unknown temporary_outage_region: {temporary}')
        if not _is_cost(spec['compensation']):
            raise ValueError(f'contract {contract_id} has invalid compensation')


def load_replica_intake(contract_path: Path, root: Path, intake_path: Path, run_id: str) -> dict:
    data = json.loads(intake_path.read_text(encoding='utf-8'))
    result = run_verification(contract_path, root, run_id)
    dataset = data.get('dataset') if isinstance(data, dict) else None
    matches = [d for d in result.datasets if d.spec.name == dataset]
    if result.status != 'pass' or len(matches) != 1 or matches[0].issue_count:
        raise ValueError('replica dataset must verify cleanly')
    validate_replica_data(data)
    discovered = discover_dataset(matches[0].spec)
    refs = {ref.path.relative_to(matches[0].spec.root_path).as_posix(): ref for ref in discovered.files}
    regions = []
    for region in data['regions']:
        ref = refs.get(region['file'])
        if ref is None:
            raise ValueError('region file missing from verified dataset')
        regions.append({**region, 'sha256': sha256_bytes(ref.path.read_bytes()), 'size_bytes': ref.path.stat().st_size})
    if set(refs) != {r['file'] for r in regions}:
        raise ValueError('not all verified files mapped to regions')
    return {'run_id': run_id, 'dataset': data['dataset'], 'contract_sha256': result.contract.sha256,
            'rulebook_sha256': result.contract.rulebook_sha256, 'regions': regions,
            'nodes': data['nodes'], 'scenarios': data['scenarios'], 'contracts': data['contracts']}


def preview_scenario_hardening(data: dict) -> dict:
    """Scenario-local inexpensive choice, not a pre-fault service commitment."""
    out = {}
    for scenario in data['scenarios']:
        failed = set(scenario['failed_nodes'])
        candidates = []
        for node in data['nodes']:
            alive = {n['node_id'] for n in data['nodes'] if n['node_id'] not in failed}
            alive.add(node['node_id'])
            if all(set(region['replicas']) & alive for region in data['regions']):
                candidates.append((node['harden_cost'], node['node_id']))
        out[scenario['scenario_id']] = {'chosen_node': min(candidates)[1] if candidates else None,
                                        'harden_cost': min(candidates)[0] if candidates else None}
    return out


def _readability(data: dict, alive: set) -> dict:
    return {r['region_id']: bool(set(r['replicas']) & alive)
            for r in sorted(data['regions'], key=lambda r: r['region_id'])}


def _evaluate_scenario(data: dict, scenario: dict, hardened, temporary, compensation):
    """Initial readability plus the cheapest recovering repair, if needed."""
    failed = set(scenario['failed_nodes']) - ({hardened} if hardened else set())
    alive = {n['node_id'] for n in data['nodes']} - failed
    initial = _readability(data, alive)
    outages = [rid for rid, ok in initial.items() if not ok]
    illegal = [rid for rid in outages if rid != temporary]
    sid = scenario['scenario_id']
    if illegal:
        return None, f"scenario '{sid}': initial outage of protected region(s) {', '.join(illegal)}"
    base = {'failed_nodes': sorted(failed), 'initial_readable': initial, 'initial_outages': outages}
    if not outages:
        return {**base, 'compensation': 0, 'repaired_node': None, 'repair_cost': 0,
                'recovered_readable': initial, 'scenario_cost': 0}, None
    costs = {n['node_id']: n['repair_cost'] for n in data['nodes']}
    best = None
    for candidate in sorted(failed):
        recovered = _readability(data, alive | {candidate})
        if all(recovered.values()) and (best is None or (costs[candidate], candidate) < best[:2]):
            best = (costs[candidate], candidate, recovered)
    if best is None:
        return None, (f"scenario '{sid}': outage of {', '.join(outages)} cannot be "
                      f'recovered with at most {MAX_REPAIRS_PER_SCENARIO} repair')
    cost, repaired, recovered = best
    return {**base, 'compensation': compensation, 'repaired_node': repaired, 'repair_cost': cost,
            'recovered_readable': recovered, 'scenario_cost': compensation + cost}, None


def _plan_contract(data: dict, contract_id: str, spec: dict) -> dict:
    temporary, compensation = spec['temporary_outage_region'], spec['compensation']
    node_ids = sorted(n['node_id'] for n in data['nodes'])
    harden = {n['node_id']: n['harden_cost'] for n in data['nodes']}
    scenarios = sorted(data['scenarios'], key=lambda s: s['scenario_id'])
    candidates = []
    for hardened in [None, *node_ids]:
        evaluated, reason = {}, None
        for scenario in scenarios:
            evaluation, reason = _evaluate_scenario(data, scenario, hardened, temporary, compensation)
            if reason is not None:
                break
            evaluated[scenario['scenario_id']] = evaluation
        if reason is not None:
            candidates.append({'hardened_node': hardened, 'feasible': False, 'total_cost': None, 'reason': reason})
            continue
        cost = 0 if hardened is None else harden[hardened]
        worst = max(item['scenario_cost'] for item in evaluated.values())
        candidates.append({'hardened_node': hardened, 'feasible': True, 'total_cost': cost + worst,
                           'reason': None, '_scenarios': evaluated, '_harden_cost': cost, '_worst': worst})
    feasible = [c for c in candidates if c['feasible']]
    if not feasible:
        reasons = '; '.join(c['reason'] for c in candidates if c['reason'])
        raise ValueError(f"contract '{contract_id}' cannot be signed: no feasible pre-fault plan ({reasons})")
    best = min(feasible, key=lambda c: (c['total_cost'], c['hardened_node'] or ''))
    shown = [{k: v for k, v in c.items() if not k.startswith('_')} for c in candidates]
    certificate = {'strategy': 'exhaustive bounded search',
                   'objective': 'minimize harden_cost + max(per-scenario '
                                'compensation + repair_cost) under the SLA',
                   'tie_break': 'equal totals pick the smallest hardened node_id; '
                                'equal repairs the smallest repair id',
                   'candidates': shown, 'optimal': True}
    return {'contract_id': contract_id, 'temporary_outage_region': temporary,
            'compensation_per_outage': compensation,
            'hardened_node': best['hardened_node'], 'harden_cost': best['_harden_cost'],
            'scenarios': best['_scenarios'], 'worst_scenario_cost': best['_worst'],
            'total_cost': best['total_cost'], 'certificate': certificate}


def plan_service_commitment(data: dict) -> dict:
    """Sign - or refuse - every service contract over the verified file set."""
    validate_replica_data(data)
    plans = {cid: _plan_contract(data, cid, data['contracts'][cid])
             for cid in sorted(data['contracts'])}
    files = sorted(({'file': r['file'], 'region_id': r['region_id'],
                     'sha256': r.get('sha256'), 'size_bytes': r.get('size_bytes')}
                    for r in data['regions']), key=lambda item: item['file'])
    limits = {'max_nodes': MAX_NODES, 'max_regions': MAX_REGIONS,
              'max_scenarios': MAX_SCENARIOS, 'max_hardened_nodes': MAX_HARDENED_NODES,
              'max_repairs_per_scenario': MAX_REPAIRS_PER_SCENARIO}
    return {'run_id': data.get('run_id'), 'dataset': data['dataset'],
            'contract_sha256': data.get('contract_sha256'),
            'rulebook_sha256': data.get('rulebook_sha256'), 'files': files,
            'limits': limits, 'plans': plans}
