"""Preview local lake partition replica hardening by known scenario."""
from __future__ import annotations
import json
from pathlib import Path
from .discovery import discover_dataset
from .engine import run_verification
from .io_utils import sha256_bytes


def load_replica_intake(contract_path:Path,root:Path,intake_path:Path,run_id:str)->dict:
    data=json.loads(intake_path.read_text(encoding='utf-8'))
    result=run_verification(contract_path,root,run_id)
    matches=[d for d in result.datasets if d.spec.name==data['dataset']]
    if result.status!='pass' or len(matches)!=1 or matches[0].issue_count:
        raise ValueError('replica dataset must verify cleanly')
    discovered=discover_dataset(matches[0].spec)
    refs={ref.path.relative_to(matches[0].spec.root_path).as_posix():ref
          for ref in discovered.files}
    regions=[]
    for region in data['regions']:
        ref=refs.get(region['file'])
        if ref is None:raise ValueError('region file missing from verified dataset')
        regions.append({**region,'sha256':sha256_bytes(ref.path.read_bytes()),
                        'size_bytes':ref.path.stat().st_size})
    if set(refs)!={r['file'] for r in regions}:
        raise ValueError('not all verified files mapped to regions')
    return {'run_id':run_id,'contract_sha256':result.contract.sha256,
            'rulebook_sha256':result.contract.rulebook_sha256,
            'regions':regions,'nodes':data['nodes'],
            'scenarios':data['scenarios'],'contracts':data['contracts']}


def preview_scenario_hardening(data:dict)->dict:
    """Scenario-local inexpensive choice, not a pre-fault service commitment."""
    out={}
    for scenario in data['scenarios']:
        failed=set(scenario['failed_nodes'])
        candidates=[]
        for node in data['nodes']:
            alive={n['node_id'] for n in data['nodes'] if n['node_id'] not in failed}
            alive.add(node['node_id'])
            if all(set(region['replicas'])&alive for region in data['regions']):
                candidates.append((node['harden_cost'],node['node_id']))
        out[scenario['scenario_id']]={'chosen_node':min(candidates)[1] if candidates else None,
                                      'harden_cost':min(candidates)[0] if candidates else None}
    return out
