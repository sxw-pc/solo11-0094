"""Verified synthetic basin monitor intake with a zero-bias preview."""
from __future__ import annotations
import csv
import json
from collections import defaultdict
from pathlib import Path
from .discovery import discover_dataset
from .engine import run_verification
from .io_utils import sha256_bytes


def load_basin_intake(contract_path:Path,root:Path,intake_path:Path,run_id:str)->dict:
    rules=json.loads(intake_path.read_text(encoding='utf-8'))
    result=run_verification(contract_path,root,run_id)
    if result.status!='pass':raise ValueError('basin monitor dataset must verify cleanly')
    by_name={d.spec.name:d for d in result.datasets}
    if set(by_name)!={rules['dataset']}:raise ValueError('basin dataset disagrees')
    dataset=by_name[rules['dataset']]
    refs=discover_dataset(dataset.spec).files
    if dataset.issue_count or len(refs)!=1:raise ValueError('one clean basin file required')
    ref=refs[0]
    with ref.path.open('r',encoding='utf-8',newline='') as stream:
        rows=list(csv.DictReader(stream))
    if len(rows)!=dataset.record_count:raise ValueError('verified row count changed')
    return {'run_id':run_id,'contract_sha256':result.contract.sha256,
            'rulebook_sha256':result.contract.rulebook_sha256,
            'file':ref.label,'file_sha256':sha256_bytes(ref.path.read_bytes()),
            'readings':rows,'rules':rules}


def preview_zero_bias(data:dict)->dict:
    """A nominal attribution only; it does not settle legal possible worlds."""
    slots=defaultdict(dict)
    for row in data['readings']:
        slots[row['event_time']][row['sensor_id']]=int(row['load_units'])
    out={}
    for slot,readings in sorted(slots.items()):
        n,s,t=(readings[name] for name in ('north','south','total'))
        out[slot]={'A':t-s,'B':n+s-t,'C':t-n}
    return out
