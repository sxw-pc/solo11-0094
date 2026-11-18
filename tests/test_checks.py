from __future__ import annotations

from tzgate.models import SEVERITIES

def _issues(run, code=None, raw=None):
    return [ issue for issue in run.issues if (code is None or issue.code == code) and (raw is None or issue.raw == raw) ]

def test_fixture_summary(run_result):
    run = run_result
    assert run.status == "fail"
    assert len(run.datasets) == 4
    assert run.files_scanned == 11
    assert run.records_scanned == 31
    assert run.by_severity == {"error": 11, "warning": 2, "info": 0}
    assert run.by_code == {
        "TZ-DST-AMBIG": 2, "TZ-DST-GAP": 2, "TZ-OFF-DRIFT": 2,
        "TZ-OFF-MISMATCH": 3, "TZ-PARSE-001": 1, "TZ-PART-001": 3,
    }
    names = [item.spec.name for item in run.datasets]
    assert names == ["events_utc", "orders_local", "sensor_offsets", "sessions_sydney"]
    assert {issue.severity for issue in run.issues} == {"error", "warning"}
    assert run.by_severity.keys() == set(SEVERITIES)

def test_dst_gap_evidence(run_result):
    issues = _issues(run_result, "TZ-DST-GAP", "2024-03-10T02:30:00")
    assert len(issues) == 1
    issue = issues[0]
    assert issue.dataset == "orders_local"
    assert issue.file.endswith("orders_local/dt=2024-03-10/part-0001.csv")
    assert issue.line == 3
    assert issue.column == "event_time"
    assert issue.check == "dst"
    assert issue.details["window_start"] == "2024-03-10T02:00:00"
    assert issue.details["window_end"] == "2024-03-10T03:00:00"
    assert issue.details["candidates"] == []

def test_dst_fold_evidence(run_result):
    issues = _issues(run_result, "TZ-DST-AMBIG", "2024-11-03T01:30:00")
    assert len(issues) == 1
    issue = issues[0]
    assert issue.line == 2
    assert [item["offset"] for item in issue.details["candidates"]] == ["-04:00", "-05:00"]
    assert issue.details["window_start"] == "2024-11-03T01:00:00"
    assert issue.details["window_end"] == "2024-11-03T02:00:00"

def test_southern_hemisphere_dst(run_result):
    assert len(_issues(run_result, "TZ-DST-AMBIG", "2024-04-07T02:30:00")) == 1
    assert len(_issues(run_result, "TZ-DST-GAP", "2024-10-06T02:30:00")) == 1

def test_partition_alignment(run_result):
    issue = _issues(run_result, "TZ-PART-001", "2024-03-09T23:30:00Z")[0]
    assert issue.file.endswith("events_utc/dt=2024-03-09/part-0001.csv")
    assert issue.line == 2
    assert issue.details == {
        "partition_date": "2024-03-09",
        "record_local_date": "2024-03-10",
        "record_local_time": "2024-03-10T00:30:00",
        "utc_instant": "2024-03-09T23:30:00Z",
        "zone": "Europe/Berlin",
        "zone_offset": "+01:00",
    }
    assert len(_issues(run_result, "TZ-PART-001", "2024-09-01T00:30:00Z")) == 1
    assert len(_issues(run_result, "TZ-PART-001", "2024-04-30T15:30:00+00:00")) == 1

def test_offset_mismatch_and_drift(run_result):
    mismatches = _issues(run_result, "TZ-OFF-MISMATCH")
    assert len(mismatches) == 3
    assert {issue.raw for issue in mismatches} == { "2024-05-01T10:00:00+00:00", "2024-05-01T11:00:00+09:00", "2024-04-30T15:30:00+00:00", }
    first = _issues(run_result, "TZ-OFF-MISMATCH", "2024-05-01T10:00:00+00:00")[0]
    assert first.details["declared_offset"] == "+00:00"
    assert first.details["expected_offset"] == "+08:00"
    assert first.details["source"] == "legacy-import"

    drift = _issues(run_result, "TZ-OFF-DRIFT")
    assert len(drift) == 2
    by_source = {issue.details["source"]: issue for issue in drift}
    assert by_source["legacy-import"].details["observed_offsets"] == ["+00:00", "+08:00"]
    assert by_source["legacy-import"].details["mismatch_count"] == 2
    assert by_source["partner-feed"].details["observed_offsets"] == ["+08:00", "+09:00"]
    assert all(issue.severity == "warning" for issue in drift)

def test_parse_error_and_shape_metadata(run_result):
    issue = _issues(run_result, "TZ-PARSE-001", "not-a-timestamp")[0]
    assert issue.line == 4
    assert issue.check == "parse"
    assert issue.details["partition"].endswith("dt=2024-03-10")

def test_issues_are_unique_and_sorted(run_result):
    ids = [issue.issue_id for issue in run_result.issues]
    assert len(ids) == len(set(ids))
    keys = [issue.sort_key for issue in run_result.issues]
    assert keys == sorted(keys)

def test_datasets_and_observed_offsets(run_result):
    by_name = {item.spec.name: item for item in run_result.datasets}
    assert by_name["events_utc"].record_count == 7
    assert by_name["orders_local"].record_count == 8
    assert by_name["sensor_offsets"].record_count == 9
    assert by_name["sessions_sydney"].record_count == 7
    assert by_name["sensor_offsets"].observed_offsets == {"+00:00", "+08:00", "+09:00"}
    assert by_name["orders_local"].observed_offsets == set()

