from pathlib import Path
from tools.fixture_data import FIXTURES
from tzgate.vessel_stowage_intake import load_stowage_intake,preview_gross_manifest

ROOT=Path(__file__).resolve().parents[1]


def test_verified_manifest_is_only_gross_preview():
    data=load_stowage_intake(ROOT/'contracts/vessel_stowage.yaml',ROOT,
                             ROOT/'fixtures/vessel_stowage/intake.json','stow-run')
    assert len(data['containers'])==4
    assert data['file_sha256']
    assert preview_gross_manifest(data)=={'count':4,'mass_tons':12,'freight_cents':24}
    assert data['rules']['contracts']['economy_rehandle']['max_rehandles_at_P1']==1
    assert data['rules']['max_post_P1_mass_imbalance_tons']==4
    assert len(FIXTURES['cargo_manifest']['files'][0][1])==4
