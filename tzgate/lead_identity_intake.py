"""Resolve lead entities and countable sales on a verified lake partition.

The intake is only usable after ``tzgate.engine.run_verification`` passes the
whole dataset: partition or file issues must never be silently ignored by the
identity layer.  On top of the verified rows this module resolves lead
entities per identity contract (person/household): a globally optimal,
tenant-safe, cannot-link-safe, evidence-connected partition plus consent
propagation that decides which sales are countable.
"""
from __future__ import annotations
import csv
import json
from itertools import combinations
from pathlib import Path
from .discovery import discover_dataset
from .engine import run_verification
from .io_utils import sha256_bytes

TOKEN_FIELDS={'email':'email_token','phone':'phone_token','account':'account_token'}
WEIGHT_FIELDS={'email':'email_evidence','phone':'phone_evidence','account':'account_evidence'}
CONSENT_VALUES=('yes','no','unknown')
MAX_EXACT_ROWS=10  # Bell(10)=115975 keeps exact partition enumeration bounded


def load_lead_intake(contract_path:Path,root:Path,intake_path:Path,run_id:str)->dict:
    result=run_verification(contract_path,root,run_id)
    config=json.loads(intake_path.read_text(encoding='utf-8'))
    datasets=[d for d in result.datasets if d.spec.name==config['dataset']]
    if len(datasets)!=1 or datasets[0].issue_count or result.status!='pass':
        raise ValueError('lead dataset must have one clean verification result')
    rows=[];files=[]
    for ref in discover_dataset(datasets[0].spec).files:
        body=ref.path.read_bytes()
        files.append({'file':ref.label,'sha256':sha256_bytes(body)})
        with ref.path.open('r',encoding='utf-8',newline='') as stream:
            rows.extend(dict(row) for row in csv.DictReader(stream))
    if len(rows)!=datasets[0].record_count or not rows:
        raise ValueError('verified row count changed')
    _validate_rows(rows)
    return {'run_id':run_id,'contract_sha256':result.contract.sha256,
            'rulebook_sha256':result.contract.rulebook_sha256,
            'dataset':config['dataset'],'files':files,'leads':rows,
            'cannot_link':_validate_cannot_link(config.get('cannot_link'),rows),
            'contracts':_validate_contracts(config.get('contracts'))}


def preview_token_components(intake:dict)->list[list[str]]:
    """Naive token transitive closure; does not enforce tenant/cannot-link."""
    parent={r['lead_id']:r['lead_id'] for r in intake['leads']}
    def find(x):
        while parent[x]!=x:x=parent[x]
        return x
    for i,left in enumerate(intake['leads']):
        for right in intake['leads'][i+1:]:
            if any(left[k]==right[k] for k in TOKEN_FIELDS.values()):
                parent[find(right['lead_id'])]=find(left['lead_id'])
    groups={}
    for row in intake['leads']:
        groups.setdefault(find(row['lead_id']),[]).append(row['lead_id'])
    return sorted(sorted(v) for v in groups.values())


def resolve_entities(intake:dict,contract_name:str)->dict:
    """Globally optimal legal entity partition plus countable-sale decisions.

    Every lead sits in exactly one group; a group never crosses tenants, never
    contains a cannot-link pair, and any multi-lead group is connected by
    direct same-token evidence edges.  The score sums the contract's evidence
    weight once per shared token kind over every intra-group pair that shares
    a token; ties break by the lexicographically smallest sorted group-ID
    list, so the result is stable under input row reordering.  The upper
    bound is the sum of all legal evidence edges and is recomputable by
    iterating lead pairs.
    """
    ctx=_prepare(intake,contract_name)
    ids=ctx['ids']
    if len(ids)>MAX_EXACT_ROWS:
        raise ValueError(f'exact search bound exceeded: {len(ids)} rows')
    best=(-1,None,None);optimal=0;evaluated=0
    for groups in _partitions(ids):
        evaluated+=1
        score=_score(groups,ctx)
        if score is None:continue
        key=tuple(groups)
        if score>best[0]:best=(score,key,groups);optimal=1
        elif score==best[0]:
            optimal+=1
            if key<best[1]:best=(score,key,groups)
    by_id={row['lead_id']:row for row in ctx['rows']}
    groups_out=[]
    for index,members in enumerate(best[2],1):
        consents=[by_id[member]['consent'] for member in members]
        countable,reason=_consent_decision(consents)
        groups_out.append({'group_id':f'g{index}','members':list(members),
                           'tenant':ctx['tenants'][members[0]],
                           'consent':{v:consents.count(v) for v in CONSENT_VALUES},
                           'countable':countable,'reason':reason})
    member_group={m:g['group_id'] for g in groups_out for m in g['members']}
    by_gid={g['group_id']:g for g in groups_out}
    edges=[{**e,'same_group':member_group[e['left']]==member_group[e['right']]}
           for e in ctx['edges']]
    sales=[]
    for row in ctx['rows']:
        if not row['sale_id']:continue
        group=by_gid[member_group[row['lead_id']]]
        sales.append({'sale_id':row['sale_id'],'lead_id':row['lead_id'],
                      'group_id':group['group_id'],'members':group['members'],
                      'countable':group['countable'],'reason':group['reason']})
    sales.sort(key=lambda sale:sale['sale_id'])
    return {'run_id':intake.get('run_id'),'dataset':intake.get('dataset'),
            'identity_contract':contract_name,'unit':ctx['spec']['unit'],
            'contract_sha256':intake.get('contract_sha256'),
            'rulebook_sha256':intake.get('rulebook_sha256'),
            'files':intake.get('files',[]),'weights':ctx['spec']['weights'],
            'groups':groups_out,'edges':edges,'rejected':ctx['rejected'],
            'sales':sales,'score':best[0],
            'upper_bound':sum(ctx['pair_weight'].values()),
            'optimal_partition_count':optimal,'partitions_evaluated':evaluated,
            'counts':{'rows':len(ids),'groups':len(groups_out),'sales':len(sales),
                      'countable_sales':sum(1 for s in sales if s['countable'])}}


def greedy_token_partition(intake:dict,contract_name:str)->dict:
    """Descending-weight greedy merge foil; not a substitute for the optimum.

    Edges are accepted in descending evidence-weight order and only when the
    merged component stays tenant-safe and cannot-link-safe.  A heavy edge can
    block two cheaper ones, so the result may fall below the global optimum.
    """
    ctx=_prepare(intake,contract_name)
    parent={lead:lead for lead in ctx['ids']}
    def find(x):
        while parent[x]!=x:x=parent[x]
        return x
    ordered=sorted(ctx['edges'],key=lambda e:(-e['weight'],e['left'],e['right']))
    for edge in ordered:
        left,right=find(edge['left']),find(edge['right'])
        if left==right:continue
        members={root:[i for i in ctx['ids'] if find(i)==root] for root in (left,right)}
        if ctx['tenants'][members[left][0]]!=ctx['tenants'][members[right][0]]:continue
        if any(tuple(sorted((a,b))) in ctx['blocked']
               for a in members[left] for b in members[right]):continue
        parent[right]=left
    groups={}
    for lead in ctx['ids']:groups.setdefault(find(lead),[]).append(lead)
    canonical=sorted(tuple(sorted(g)) for g in groups.values())
    return {'groups':[list(g) for g in canonical],'score':_score(canonical,ctx)}


def _prepare(intake:dict,contract_name:str)->dict:
    rows=intake['leads'];_validate_rows(rows)
    cannot_link=_validate_cannot_link(intake.get('cannot_link'),rows)
    contracts=_validate_contracts(intake.get('contracts'))
    if contract_name not in contracts:
        raise ValueError(f'unknown identity contract: {contract_name}')
    ordered=sorted(rows,key=lambda row:row['lead_id'])
    ids=[row['lead_id'] for row in ordered]
    pairs=_evidence_pairs(ordered,cannot_link)
    edges=[pair for pair in pairs if not pair['reasons']]
    weights=contracts[contract_name]['weights']
    for edge in edges:
        edge['weight']=sum(weights[token] for token in edge['tokens'])
    adjacency={lead:set() for lead in ids}
    for pair in pairs:
        adjacency[pair['left']].add(pair['right'])
        adjacency[pair['right']].add(pair['left'])
    return {'rows':ordered,'ids':ids,'spec':contracts[contract_name],
            'edges':edges,'rejected':[p for p in pairs if p['reasons']],
            'pair_weight':{(e['left'],e['right']):e['weight'] for e in edges},
            'adjacency':adjacency,'blocked':{tuple(p) for p in cannot_link},
            'tenants':{row['lead_id']:row['tenant'] for row in ordered}}


def _evidence_pairs(rows:list[dict],cannot_link:list[list[str]])->list[dict]:
    blocked={tuple(pair) for pair in cannot_link}
    pairs=[]
    for i,left in enumerate(rows):
        for right in rows[i+1:]:
            shared=[n for n,f in TOKEN_FIELDS.items() if left[f]==right[f]]
            if not shared:continue
            reasons=[]
            if left['tenant']!=right['tenant']:reasons.append('tenant_mismatch')
            if (left['lead_id'],right['lead_id']) in blocked:reasons.append('cannot_link')
            pairs.append({'left':left['lead_id'],'right':right['lead_id'],
                          'tokens':shared,'reasons':reasons})
    return pairs


def _partitions(ids:list[str]):
    """Yield each set partition of the sorted ids exactly once (RGS order)."""
    groups=[]
    def visit(i):
        if i==len(ids):
            yield [tuple(g) for g in groups];return
        for g in groups:
            g.append(ids[i]);yield from visit(i+1);g.pop()
        groups.append([ids[i]]);yield from visit(i+1);groups.pop()
    yield from visit(0)


def _score(groups,ctx):
    """Score a candidate partition, or return None when it is not legal."""
    total=0
    for group in groups:
        if len({ctx['tenants'][m] for m in group})!=1:return None
        pairs=list(combinations(group,2))
        if any(pair in ctx['blocked'] for pair in pairs):return None
        if len(group)>1 and not _connected(group,ctx['adjacency']):return None
        total+=sum(ctx['pair_weight'].get(pair,0) for pair in pairs)
    return total


def _connected(group,adjacency)->bool:
    members=set(group);seen={group[0]};stack=[group[0]]
    while stack:
        for other in adjacency[stack.pop()]&members-seen:
            seen.add(other);stack.append(other)
    return seen==members


def _consent_decision(consents:list[str])->tuple[bool,str]:
    if 'no' in consents:return False,'consent_no_veto'
    if 'yes' in consents:return True,'consent_yes_present'
    return False,'consent_unknown_only'


def _validate_rows(rows:list[dict])->None:
    ids=[];sale_ids=[]
    for row in rows:
        lead=(row.get('lead_id') or '').strip()
        tenant=(row.get('tenant') or '').strip()
        if not lead:raise ValueError('empty lead ID')
        if not tenant:raise ValueError(f'empty tenant for lead {lead}')
        row['lead_id']=lead;row['tenant']=tenant
        for field in TOKEN_FIELDS.values():
            token=(row.get(field) or '').strip()
            if not token:raise ValueError(f'empty {field} for lead {lead}')
            row[field]=token
        consent=(row.get('consent') or '').strip()
        if consent not in CONSENT_VALUES:
            raise ValueError(f'invalid consent for lead {lead}: {consent!r}')
        row['consent']=consent
        sale=(row.get('sale_id') or '').strip()
        row['sale_id']=sale
        ids.append(lead)
        if sale:sale_ids.append(sale)
    if len(ids)!=len(set(ids)):raise ValueError('duplicate lead ID')
    if len(sale_ids)!=len(set(sale_ids)):raise ValueError('duplicate sale ID')


def _validate_cannot_link(pairs,rows:list[dict])->list[list[str]]:
    if not isinstance(pairs,list):
        raise ValueError('cannot_link must be a list of lead pairs')
    known={row['lead_id'] for row in rows};normalized=set()
    for pair in pairs:
        if not isinstance(pair,(list,tuple)) or len(pair)!=2:
            raise ValueError('cannot_link entries must be pairs of lead IDs')
        left,right=pair
        if left==right:raise ValueError(f'cannot_link self pair: {left}')
        for member in (left,right):
            if member not in known:
                raise ValueError(f'cannot_link references unknown lead: {member}')
        normalized.add(tuple(sorted((left,right))))
    return sorted(normalized)


def _validate_contracts(contracts)->dict:
    if not isinstance(contracts,dict) or not contracts:
        raise ValueError('identity contracts missing')
    validated={}
    for name,spec in contracts.items():
        if not isinstance(spec,dict):
            raise ValueError(f'identity contract {name} must be a mapping')
        raw=spec.get('weights')
        if not isinstance(raw,dict):  # raw intake.json form, not yet normalized
            raw={token:spec.get(key) for token,key in WEIGHT_FIELDS.items()}
        weights={}
        for token,key in WEIGHT_FIELDS.items():
            value=raw.get(token)
            if isinstance(value,bool) or not isinstance(value,int) or value<0:
                raise ValueError(f'invalid evidence weight {key} for {name}')
            weights[token]=value
        unit=spec.get('unit')
        if not isinstance(unit,str) or not unit.strip():
            raise ValueError(f'invalid unit for identity contract {name}')
        validated[name]={'weights':weights,'unit':unit.strip()}
    return validated
