"""Verified synthetic cargo manifest intake and gross manifest preview."""
from __future__ import annotations
import csv
import json
from pathlib import Path
from .discovery import discover_dataset
from .engine import run_verification
from .io_utils import sha256_bytes


def load_stowage_intake(contract_path:Path,root:Path,intake_path:Path,run_id:str)->dict:
    rules=json.loads(intake_path.read_text(encoding='utf-8'))
    result=run_verification(contract_path,root,run_id)
    if result.status!='pass':raise ValueError('cargo manifest must verify cleanly')
    by_name={d.spec.name:d for d in result.datasets}
    if set(by_name)!={rules['dataset']}:raise ValueError('cargo dataset disagrees')
    dataset=by_name[rules['dataset']]
    refs=discover_dataset(dataset.spec).files
    if dataset.issue_count or len(refs)!=1:raise ValueError('one clean cargo file required')
    ref=refs[0]
    with ref.path.open('r',encoding='utf-8',newline='') as stream:
        rows=list(csv.DictReader(stream))
    if len(rows)!=dataset.record_count:raise ValueError('verified row count changed')
    return {'run_id':run_id,'contract_sha256':result.contract.sha256,
            'rulebook_sha256':result.contract.rulebook_sha256,
            'file':ref.label,'file_sha256':sha256_bytes(ref.path.read_bytes()),
            'containers':rows,'rules':rules}


def preview_gross_manifest(data:dict)->dict:
    """Gross available cargo without a safe stowage or unload sequence."""
    return {'count':len(data['containers']),
            'mass_tons':sum(int(row['mass_tons']) for row in data['containers']),
            'freight_cents':sum(int(row['freight_cents']) for row in data['containers'])}
