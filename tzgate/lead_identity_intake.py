"""Inspect a verified synthetic lake partition of CRM lead forms."""
from __future__ import annotations
import csv
import json
from pathlib import Path
from .discovery import discover_dataset
from .engine import run_verification
from .io_utils import sha256_bytes


def load_lead_intake(contract_path:Path,root:Path,intake_path:Path,run_id:str)->dict:
    result=run_verification(contract_path,root,run_id)
    config=json.loads(intake_path.read_text(encoding='utf-8'))
    datasets=[d for d in result.datasets if d.spec.name==config['dataset']]
    if len(datasets)!=1 or datasets[0].issue_count or result.status!='pass':
        raise ValueError('lead dataset must have one clean verification result')
    discovered=discover_dataset(datasets[0].spec)
    rows=[];files=[]
    for ref in discovered.files:
        body=ref.path.read_bytes()
        files.append({'file':ref.label,'sha256':sha256_bytes(body)})
        with ref.path.open('r',encoding='utf-8',newline='') as stream:
            rows.extend(dict(row) for row in csv.DictReader(stream))
    if len(rows)!=datasets[0].record_count or not rows:
        raise ValueError('verified row count changed')
    ids=[row['lead_id'] for row in rows]
    if len(ids)!=len(set(ids)):
        raise ValueError('duplicate lead ID')
    return {'run_id':run_id,'contract_sha256':result.contract.sha256,
            'rulebook_sha256':result.contract.rulebook_sha256,
            'dataset':config['dataset'],'files':files,'leads':rows,
            'cannot_link':config['cannot_link'],'contracts':config['contracts']}


def preview_token_components(intake:dict)->list[list[str]]:
    """Naive token transitive closure; does not enforce tenant/cannot-link."""
    parent={r['lead_id']:r['lead_id'] for r in intake['leads']}
    def find(x):
        while parent[x]!=x:x=parent[x]
        return x
    for i,left in enumerate(intake['leads']):
        for right in intake['leads'][i+1:]:
            if any(left[k]==right[k] for k in ('email_token','phone_token','account_token')):
                parent[find(right['lead_id'])]=find(left['lead_id'])
    groups={}
    for row in intake['leads']:
        groups.setdefault(find(row['lead_id']),[]).append(row['lead_id'])
    return sorted(sorted(v) for v in groups.values())
