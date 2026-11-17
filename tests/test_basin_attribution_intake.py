from pathlib import Path
from tools.fixture_data import FIXTURES
from tzgate.basin_attribution_intake import load_basin_intake,preview_zero_bias

ROOT=Path(__file__).resolve().parents[1]


def test_verified_basin_intake_has_nominal_only_preview():
    data=load_basin_intake(ROOT/'contracts/basin_attribution.yaml',ROOT,
                           ROOT/'fixtures/basin_attribution/intake.json','basin-run')
    assert len(data['readings'])==6
    assert data['file_sha256']
    assert data['rules']['bias_constant_across_slots'] is True
    assert len(FIXTURES['basin_monitors']['files'][0][1])==6
    assert list(preview_zero_bias(data).values())==[
        {'A':4,'B':3,'C':3}, {'A':5,'B':3,'C':3}]
