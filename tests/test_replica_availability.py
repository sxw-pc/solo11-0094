from pathlib import Path
from tzgate.replica_availability import load_replica_intake,preview_scenario_hardening

ROOT=Path(__file__).resolve().parents[1]
CONTRACT=ROOT/'contracts/replica_availability.yaml'
INTAKE=ROOT/'fixtures/replica_availability/intake.json'


def test_verified_partition_files_drive_local_hardening_preview():
    data=load_replica_intake(CONTRACT,ROOT,INTAKE,'replica-run')
    assert [r['region_id'] for r in data['regions']]==['ca','eu','us']
    assert all(r['sha256'] and r['size_bytes']>0 for r in data['regions'])
    assert preview_scenario_hardening(data)=={
        'west':{'chosen_node':'A','harden_cost':2},
        'east':{'chosen_node':'C','harden_cost':2}}


def test_fixture_generator_includes_verified_replica_partitions():
    from tools.fixture_data import FIXTURES
    assert 'replica_orders' in FIXTURES
    assert len(FIXTURES['replica_orders']['files'])==3
