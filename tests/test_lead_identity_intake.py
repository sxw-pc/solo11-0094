from pathlib import Path
from tzgate.lead_identity_intake import load_lead_intake,preview_token_components

ROOT=Path(__file__).resolve().parents[1]
CONTRACT=ROOT/'contracts/lead_identity.yaml'
INTAKE=ROOT/'fixtures/lead_identity/intake.json'


def test_verified_lake_lead_partition_has_conflicting_identity_components():
    result=load_lead_intake(CONTRACT,ROOT,INTAKE,'lead-test-run')
    assert result['dataset']=='lead_forms'
    assert len(result['files'])==1 and result['files'][0]['sha256']
    assert len(result['leads'])==8
    assert result['leads'][1]['sale_id']=='S2'
    assert result['leads'][6]['sale_id']=='S7'
    assert preview_token_components(result)==[['r1','r2','r3'],['r4','r5','r6','r7','r8']]
    assert result['contract_sha256'] and result['rulebook_sha256']



def test_partition_error_blocks_identity_intake(tmp_path):
    import shutil
    import pytest
    for rel in ['contracts/lead_identity.yaml','contracts/timezone_rules.yaml',
                'fixtures/lead_identity/intake.json',
                'fixtures/lead_identity/lake/dt=2024-07-01/part-0001.csv']:
        target=tmp_path/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/rel,target)
    bad=tmp_path/'fixtures/lead_identity/lake/dt=2024-07-01/part-0001.csv'
    bad.write_text(bad.read_text(encoding='utf-8').replace('2024-07-01T12:00:00Z',
                                                        '2024-07-02T12:00:00Z',1),encoding='utf-8')
    with pytest.raises(ValueError,match='clean verification'):
        load_lead_intake(tmp_path/'contracts/lead_identity.yaml',tmp_path,
                         tmp_path/'fixtures/lead_identity/intake.json','lead-bad-run')
