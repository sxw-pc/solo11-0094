from pathlib import Path
from tzgate.lake_license_intake import load_license_intake,preview_single_region
from tools.fixture_data import FIXTURES

ROOT=Path(__file__).resolve().parents[1]
CONTRACT=ROOT/'contracts/lake_license.yaml'
INTAKE=ROOT/'fixtures/lake_license/intake.json'


def test_verified_regions_and_unsafe_single_region_quote():
    data=load_license_intake(CONTRACT,ROOT,INTAKE,'license-run')
    assert [(r['region_id'],r['row_count']) for r in data['regions']]==[
        ('ca',20),('eu',70),('us',160)]
    assert all(r['sha256'] and r['size_bytes']>0 for r in data['regions'])
    preview=preview_single_region(data)
    assert [(r['region_id'],r['bid_id']) for r in preview['quotes']]==[
        ('ca','b6'),('eu','b7'),('us','b4')]
    assert preview['quoted_total_cents']==2600
    assert preview['settlement_computed'] is False


def test_generator_declares_all_three_lake_regions():
    assert all(f'license_{r}' in FIXTURES for r in ('ca','eu','us'))
    assert [len(FIXTURES[f'license_{r}']['files'][0][1]) for r in ('ca','eu','us')]==[20,70,160]
