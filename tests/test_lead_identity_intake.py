import csv
import json
import shutil
from pathlib import Path

import pytest

from tzgate.lead_identity_intake import (greedy_token_partition, load_lead_intake,
                                         preview_token_components, resolve_entities)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/lead_identity.yaml"
INTAKE = ROOT / "fixtures/lead_identity/intake.json"
CSV_REL = "fixtures/lead_identity/lake/dt=2024-07-01/part-0001.csv"
TREE_FILES = ["contracts/lead_identity.yaml", "contracts/timezone_rules.yaml",
              "fixtures/lead_identity/intake.json", CSV_REL]


def _load(run_id="lead-test-run"):
    return load_lead_intake(CONTRACT, ROOT, INTAKE, run_id)


def _set(lead, **changes):
    return lambda row: {**row, **changes} if row["lead_id"] == lead else row


def _tree(tmp_path, edit_rows=None, edit_config=None):
    for rel in TREE_FILES:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, target)
    if edit_rows is not None:
        path = tmp_path / CSV_REL
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            header, rows = reader.fieldnames, [edit_rows(dict(r)) for r in reader]
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=header, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    if edit_config is not None:
        path = tmp_path / "fixtures/lead_identity/intake.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(edit_config(config)), encoding="utf-8")
    return (tmp_path / "contracts/lead_identity.yaml", tmp_path,
            tmp_path / "fixtures/lead_identity/intake.json")


def _groups(report):
    return [group["members"] for group in report["groups"]]


def _sale(report, sale_id):
    return next(sale for sale in report["sales"] if sale["sale_id"] == sale_id)


def _row(lead, email, phone, account):
    return {"event_time": "2024-07-01T12:00:00Z", "source_id": "forms-1",
            "lead_id": lead, "tenant": "A", "email_token": email,
            "phone_token": phone, "account_token": account,
            "consent": "unknown", "sale_id": ""}


def _synthetic(rows, cannot_link, weights):
    contract = dict(zip(("email_evidence", "phone_evidence", "account_evidence"), weights))
    contract["unit"] = "individual"
    return {"run_id": "synthetic", "dataset": "lead_forms", "contract_sha256": "",
            "rulebook_sha256": "", "files": [], "leads": rows,
            "cannot_link": cannot_link, "contracts": {"unit_weights": contract}}


def test_verified_lake_lead_partition_has_conflicting_identity_components():
    result = _load()
    assert result["dataset"] == "lead_forms"
    assert len(result["files"]) == 1 and result["files"][0]["sha256"]
    assert len(result["leads"]) == 8
    assert result["leads"][1]["sale_id"] == "S2"
    assert result["leads"][6]["sale_id"] == "S7"
    assert preview_token_components(result) == [["r1", "r2", "r3"],
                                                ["r4", "r5", "r6", "r7", "r8"]]
    assert result["contract_sha256"] and result["rulebook_sha256"]


def test_partition_error_blocks_identity_intake(tmp_path):
    contract, root, intake = _tree(tmp_path, edit_rows=_set("r1", event_time="2024-07-02T12:00:00Z"))
    with pytest.raises(ValueError, match="clean verification"):
        load_lead_intake(contract, root, intake, "lead-bad-run")


def test_file_verification_issue_is_not_silently_ignored(tmp_path):
    contract, root, intake = _tree(tmp_path)
    extra = root / "fixtures/lead_identity/lake/dt=2024-07-01/part-0002.csv"
    extra.write_text((root / CSV_REL).read_text(encoding="utf-8").replace(
        "2024-07-01T12:00:00Z", "2024-07-03T12:00:00Z"), encoding="utf-8")
    with pytest.raises(ValueError, match="clean verification"):
        load_lead_intake(contract, root, intake, "lead-extra-file")


def test_preview_transitive_union_is_not_a_legal_entity_partition():
    intake = _load()
    preview = preview_token_components(intake)
    assert ["r1", "r2", "r3"] in preview  # cannot-link pair r1/r3 merged
    assert ["r4", "r5", "r6", "r7", "r8"] in preview  # tenants A and B spanned
    for name in ("person_identity", "household_identity"):
        groups = _groups(resolve_entities(intake, name))
        assert groups != preview and ["r8"] in groups
        for pair in (("r1", "r3"), ("r4", "r6"), ("r5", "r7")):
            assert all(not set(pair) <= set(group) for group in groups)


def test_person_identity_global_optimum_and_countable_sales():
    intake = _load("lead-person-run")
    report = resolve_entities(intake, "person_identity")
    assert report["run_id"] == "lead-person-run"
    assert report["contract_sha256"] == intake["contract_sha256"]
    assert report["rulebook_sha256"] == intake["rulebook_sha256"]
    assert report["files"] == intake["files"] and report["unit"] == "individual"
    assert report["weights"] == {"email": 5, "phone": 1, "account": 1}
    assert report["score"] == 7 and report["optimal_partition_count"] == 1
    assert report["upper_bound"] == 9 >= report["score"]
    assert report["partitions_evaluated"] == 4140  # Bell(8): bounded exact search
    assert _groups(report) == [["r1", "r2"], ["r3"], ["r4", "r5"], ["r6", "r7"], ["r8"]]
    edges = {(e["left"], e["right"]): e for e in report["edges"]}
    assert edges[("r1", "r2")]["weight"] == 5 and edges[("r1", "r2")]["same_group"]
    assert edges[("r2", "r3")]["weight"] == 1 and not edges[("r2", "r3")]["same_group"]
    assert edges[("r4", "r5")]["same_group"] and edges[("r6", "r7")]["same_group"]
    assert not edges[("r5", "r6")]["same_group"]
    assert report["counts"]["countable_sales"] == 2
    for sale_id, members in (("S2", ["r1", "r2"]), ("S7", ["r6", "r7"])):
        sale = _sale(report, sale_id)
        assert sale["countable"] and sale["reason"] == "consent_yes_present"
        assert sale["members"] == members


def test_household_identity_global_optimum_and_consent_veto():
    report = resolve_entities(_load(), "household_identity")
    assert report["unit"] == "household"
    assert report["weights"] == {"email": 2, "phone": 6, "account": 7}
    assert report["score"] == 18 and report["optimal_partition_count"] == 1
    assert report["upper_bound"] == 27 >= report["score"]
    assert _groups(report) == [["r1"], ["r2", "r3"], ["r4", "r5"], ["r6", "r7"], ["r8"]]
    sale = _sale(report, "S2")  # r3 said no: the whole {r2,r3} group is vetoed
    assert sale["members"] == ["r2", "r3"]
    assert not sale["countable"] and sale["reason"] == "consent_no_veto"
    sale = _sale(report, "S7")
    assert sale["countable"] and sale["reason"] == "consent_yes_present"
    assert report["counts"]["countable_sales"] == 1


def test_greedy_descending_edges_fall_below_global_optimum():
    intake = _load()
    greedy = greedy_token_partition(intake, "household_identity")
    # The 7-weight account edge r5-r6 is taken first, then blocks the two
    # 6-weight phone edges r4-r5 and r6-r7 via cannot-link: 7 kept, 12 lost.
    assert greedy["groups"] == [["r1"], ["r2", "r3"], ["r4"], ["r5", "r6"], ["r7"], ["r8"]]
    assert greedy["score"] == 13
    optimal = resolve_entities(intake, "household_identity")
    assert optimal["score"] == 18 > greedy["score"]
    chain = {"r4", "r5", "r6", "r7"}

    def kept(groups):
        return sum(e["weight"] for e in optimal["edges"]
                   if {e["left"], e["right"]} <= chain
                   and any(e["left"] in g and e["right"] in g for g in groups))

    assert kept(greedy["groups"]) == 7
    assert kept(_groups(optimal)) == 12


def test_tenant_and_cannot_link_rejections_are_recorded(tmp_path):
    report = resolve_entities(_load(), "household_identity")
    rejected = {(r["left"], r["right"]): r for r in report["rejected"]}
    assert rejected[("r7", "r8")]["reasons"] == ["tenant_mismatch"]
    assert rejected[("r7", "r8")]["tokens"] == ["email", "phone", "account"]
    assert rejected[("r6", "r8")]["tokens"] == ["phone"]
    assert all(edge["right"] != "r8" for edge in report["edges"])
    # Give r3 the email token of its cannot-link partner r1: still no merge.
    contract, root, intake = _tree(tmp_path, edit_rows=_set("r3", email_token="e1"))
    report = resolve_entities(load_lead_intake(contract, root, intake, "lead-cl"),
                              "person_identity")
    rejected = {(r["left"], r["right"]): r for r in report["rejected"]}
    assert rejected[("r1", "r3")]["reasons"] == ["cannot_link"]
    assert rejected[("r1", "r3")]["tokens"] == ["email"]
    assert all(not {"r1", "r3"} <= set(group) for group in _groups(report))


def test_csv_token_consent_sale_changes_flow_through_resolution(tmp_path):
    # r6 yes->unknown: {r6,r7} loses its only yes, so S7 becomes uncountable.
    contract, root, intake = _tree(tmp_path / "a", edit_rows=_set("r6", consent="unknown"))
    report = resolve_entities(load_lead_intake(contract, root, intake, "lead-c1"),
                              "person_identity")
    sale = _sale(report, "S7")
    assert not sale["countable"] and sale["reason"] == "consent_unknown_only"
    assert report["counts"]["countable_sales"] == 1
    # r8 tenant B->A: r8 joins {r6,r7}; its 'no' vetoes S7 under household.
    contract, root, intake = _tree(tmp_path / "b", edit_rows=_set("r8", tenant="A"))
    report = resolve_entities(load_lead_intake(contract, root, intake, "lead-c2"),
                              "household_identity")
    assert ["r6", "r7", "r8"] in _groups(report) and report["rejected"] == []
    sale = _sale(report, "S7")
    assert sale["members"] == ["r6", "r7", "r8"] and not sale["countable"]
    assert report["counts"]["countable_sales"] == 0
    # Moving S2 from r2 to r3 moves it into the vetoed singleton {r3}.
    def move_sale(row):
        if row["lead_id"] == "r2":
            return {**row, "sale_id": ""}
        if row["lead_id"] == "r3":
            return {**row, "sale_id": "S2"}
        return row

    contract, root, intake = _tree(tmp_path / "c", edit_rows=move_sale)
    report = resolve_entities(load_lead_intake(contract, root, intake, "lead-c3"),
                              "person_identity")
    sale = _sale(report, "S2")
    assert sale["lead_id"] == "r3" and sale["members"] == ["r3"]
    assert not sale["countable"] and sale["reason"] == "consent_no_veto"


@pytest.mark.parametrize("edit,match", [
    (_set("r8", lead_id="r7"), "duplicate lead ID"),
    (_set("r3", sale_id="S2"), "duplicate sale ID"),
    (_set("r1", consent="maybe"), "invalid consent"),
    (_set("r1", email_token=""), "empty email_token"),
    (_set("r1", tenant=""), "empty tenant"),
])
def test_invalid_rows_rejected(tmp_path, edit, match):
    contract, root, intake = _tree(tmp_path, edit_rows=edit)
    with pytest.raises(ValueError, match=match):
        load_lead_intake(contract, root, intake, "lead-invalid")


@pytest.mark.parametrize("bad_weight", [-1, "5", True, 2.5])
def test_invalid_evidence_weight_rejected(tmp_path, bad_weight):
    def edit(config):
        config["contracts"]["person_identity"]["email_evidence"] = bad_weight
        return config

    contract, root, intake = _tree(tmp_path, edit_config=edit)
    with pytest.raises(ValueError, match="invalid evidence weight"):
        load_lead_intake(contract, root, intake, "lead-bad-weight")


def test_cannot_link_must_reference_real_distinct_leads(tmp_path):
    cases = (([["r1", "r9"]], "unknown lead"), ([["r1", "r1"]], "self pair"))
    for pairs, match in cases:
        contract, root, intake = _tree(
            tmp_path, edit_config=lambda config: {**config, "cannot_link": pairs})
        with pytest.raises(ValueError, match=match):
            load_lead_intake(contract, root, intake, "lead-bad-pair")


def test_equal_scores_break_tie_by_sorted_group_id_list():
    # x1-x2 share email, x2-x3 share phone, x1/x3 cannot link: two legal
    # partitions tie at score 1, so the sorted group-ID list decides.
    intake = _synthetic([_row("x1", "e1", "p0", "a1"), _row("x2", "e1", "p1", "a2"),
                         _row("x3", "e3", "p1", "a3")], [["x1", "x3"]], (1, 1, 1))
    report = resolve_entities(intake, "unit_weights")
    assert report["score"] == 1 and report["optimal_partition_count"] == 2
    assert _groups(report) == [["x1"], ["x2", "x3"]]


def test_row_order_permutation_does_not_change_resolution(tmp_path):
    contract, root, intake = _tree(tmp_path)
    path = root / CSV_REL
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0]] + lines[1:][::-1]) + "\n", encoding="utf-8")
    shuffled = load_lead_intake(contract, root, intake, "lead-reversed")
    keys = ("groups", "edges", "rejected", "sales", "score", "upper_bound", "counts")
    for name in ("person_identity", "household_identity"):
        expected, actual = resolve_entities(_load(), name), resolve_entities(shuffled, name)
        for key in keys:
            assert actual[key] == expected[key]


def test_exact_search_bound_rejects_oversized_intake():
    rows = [_row(f"x{i:02d}", f"e{i}", f"p{i}", f"a{i}") for i in range(11)]
    with pytest.raises(ValueError, match="exact search bound"):
        resolve_entities(_synthetic(rows, [], (1, 1, 1)), "unit_weights")
