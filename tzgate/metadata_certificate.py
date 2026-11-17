"""Preview document metadata fields in one verified lake dataset sample."""
from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path
from .discovery import discover_dataset
from .engine import run_verification
from .io_utils import sha256_bytes


def load_frame(path:Path)->dict:
    frame=json.loads(path.read_text(encoding='utf-8'))
    ids=frame['document_ids'];n=frame['sample_size']
    if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)):
        raise ValueError('document frame IDs invalid')
    if type(n) is not int or n<1 or n>len(ids) or not frame['seed']:
        raise ValueError('sample plan invalid')
    return frame


def draw_frame(frame:dict)->list[str]:
    return sorted(frame['document_ids'],key=lambda doc:(
        hashlib.sha256(f"{frame['seed']}|{doc}".encode('utf-8')).hexdigest(),doc
    ))[:frame['sample_size']]


def preview_metadata(contract_path:Path,root:Path,frame_path:Path,run_id:str)->dict:
    frame=load_frame(frame_path)
    result=run_verification(contract_path,root,run_id)
    datasets=[d for d in result.datasets if d.spec.name==frame['dataset']]
    if len(datasets)!=1 or result.status!='pass' or datasets[0].issue_count:
        raise ValueError('metadata dataset must have one clean verification result')
    files=discover_dataset(datasets[0].spec).files
    records=[];file_hashes=[]
    for ref in files:
        file_hashes.append({'file':ref.label,'sha256':sha256_bytes(ref.path.read_bytes())})
        with ref.path.open('r',encoding='utf-8',newline='') as stream:
            records.extend(dict(r) for r in csv.DictReader(stream))
    ids=[r['document_id'] for r in records]
    if len(ids)!=len(set(ids)) or set(ids)!=set(frame['document_ids']):
        raise ValueError('frame and verified document IDs disagree')
    selected=draw_frame(frame)
    rows=[next(r for r in records if r['document_id']==doc) for doc in selected]
    return {'run_id':run_id,'contract_sha256':result.contract.sha256,
            'rulebook_sha256':result.contract.rulebook_sha256,
            'files':file_hashes,'frame_size':len(ids),'sample_size':len(selected),
            'selected_ids':selected,
            'full_metadata_count':sum(all(r.get(k,'').strip() for k in frame['contracts']['full_metadata']['required_fields']) for r in rows),
            'public_index_count':sum(all(r.get(k,'').strip() for k in frame['contracts']['public_index']['required_fields']) for r in rows),
            'population_certificate':None}
