"""Preview single-region bids on verified synthetic lake datasets."""
from __future__ import annotations
import json
from pathlib import Path
from .discovery import discover_dataset
from .engine import run_verification
from .io_utils import sha256_bytes


def load_license_intake(contract_path:Path,root:Path,intake_path:Path,run_id:str)->dict:
    data=json.loads(intake_path.read_text(encoding='utf-8'))
    result=run_verification(contract_path,root,run_id)
    if result.status!='pass':raise ValueError('license datasets must verify cleanly')
    by_name={d.spec.name:d for d in result.datasets}
    if set(by_name)!=set(data['regions'].values()):
        raise ValueError('license region datasets disagree')
    regions=[]
    for region,dataset in sorted(data['regions'].items()):
        d=by_name[dataset]
        refs=discover_dataset(d.spec).files
        if d.issue_count or len(refs)!=1:raise ValueError('region must have one clean file')
        ref=refs[0]
        regions.append({'region_id':region,'dataset':dataset,
                        'row_count':d.record_count,'file':ref.label,
                        'sha256':sha256_bytes(ref.path.read_bytes()),
                        'size_bytes':ref.path.stat().st_size})
    return {'run_id':run_id,'contract_sha256':result.contract.sha256,
            'rulebook_sha256':result.contract.rulebook_sha256,
            'regions':regions,'bids':data['bids'],'contracts':data['contracts']}


def preview_single_region(data:dict)->dict:
    """Local single-region price quote, not an exclusive license sale."""
    chosen=[]
    for region in data['regions']:
        options=[bid for bid in data['bids'] if bid['regions']==[region['region_id']]]
        if options:
            bid=min(options,key=lambda b:(-b['value_cents'],b['bid_id']))
            chosen.append({'region_id':region['region_id'],'bid_id':bid['bid_id'],
                           'licensee':bid['licensee'],'value_cents':bid['value_cents']})
    return {'quotes':chosen,'quoted_total_cents':sum(r['value_cents'] for r in chosen),
            'settlement_computed':False}
