from pathlib import Path
from tools.fixture_data import FIXTURES
from tzgate.network_clearing_intake import load_clearing_intake,preview_gross_liabilities

ROOT=Path(__file__).resolve().parents[1]


def test_verified_debt_network_is_only_gross_preview():
    data=load_clearing_intake(ROOT/'contracts/network_clearing.yaml',ROOT,
                              ROOT/'fixtures/network_clearing/intake.json','clear-run')
    assert len(data['debts'])==6
    assert data['file_sha256']
    assert data['rules']['regimes']['senior_first']['fixed_point']=='greatest'
    assert preview_gross_liabilities(data)=={'A':200,'B':100,'C':50,'D':30,'E':30}
    assert len(FIXTURES['clearing_debts']['files'][0][1])==6
