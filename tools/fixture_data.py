"""Synthetic lake contents used by the committed fixtures.

All values are fabricated. Times are chosen to exercise the four rulebook
zones and the three detectors: partition-day mismatch, DST gap/fold and
same-source offset drift.
"""

FIXTURES = {
    "events_utc": {
        "header": ("event_id", "event_time", "source_id"),
        "files": (
            (
                "dt=2024-03-09/part-0001.csv",
                (
                    ("evt-1001", "2024-03-09T23:30:00Z", "feed-eu"),
                    ("evt-1002", "2024-03-09T22:00:00Z", "feed-eu"),
                ),
            ),
            (
                "dt=2024-03-10/part-0001.csv",
                (
                    ("evt-1003", "2024-03-10T00:30:00Z", "feed-eu"),
                    ("evt-1004", "2024-03-10T22:30:00Z", "feed-eu"),
                    ("evt-1005", "not-a-timestamp", "feed-eu"),
                ),
            ),
            (
                "dt=2024-08-31/part-0001.csv",
                (
                    ("evt-1006", "2024-09-01T00:30:00Z", "feed-us"),
                    ("evt-1007", "2024-08-31T21:00:00Z", "feed-us"),
                ),
            ),
        ),
    },
    "orders_local": {
        "header": ("order_id", "event_time", "source_id"),
        "files": (
            (
                "dt=2024-03-10/part-0001.csv",
                (
                    ("ord-1001", "2024-03-10T01:30:00", "web-east"),
                    ("ord-1002", "2024-03-10T02:30:00", "web-east"),
                    ("ord-1003", "2024-03-10T03:30:00", "web-east"),
                ),
            ),
            (
                "dt=2024-11-03/part-0001.csv",
                (
                    ("ord-1101", "2024-11-03T01:30:00", "web-east"),
                    ("ord-1102", "2024-11-03T02:30:00", "web-east"),
                ),
            ),
            (
                "dt=2024-06-01/part-0001.csv",
                (
                    ("ord-0601", "2024-06-01T09:00:00", "web-east"),
                    ("ord-0602", "2024-06-01T12:00:00", "web-west"),
                    ("ord-0603", "2024-06-01T18:30:00", "web-west"),
                ),
            ),
        ),
    },
    "sensor_offsets": {
        "header": ("sensor_id", "event_time", "source_id"),
        "files": (
            (
                "dt=2024-05-01/part-0001.csv",
                (
                    ("sen-2001", "2024-05-01T08:00:00+08:00", "gateway-a"),
                    ("sen-2002", "2024-05-01T09:15:00+08:00", "gateway-a"),
                    ("sen-2003", "2024-05-01T10:00:00+00:00", "legacy-import"),
                    ("sen-2004", "2024-05-01T11:00:00+09:00", "partner-feed"),
                    ("sen-2005", "2024-05-01T12:00:00+08:00", "partner-feed"),
                    ("sen-2006", "2024-05-01T13:00:00+08:00", "legacy-import"),
                    ("sen-2007", "2024-04-30T15:30:00+00:00", "legacy-import"),
                ),
            ),
            (
                "dt=2024-05-02/part-0001.csv",
                (
                    ("sen-2008", "2024-05-02T09:00:00+08:00", "gateway-a"),
                    ("sen-2009", "2024-05-02T10:00:00+08:00", "partner-feed"),
                ),
            ),
        ),
    },
    "sessions_sydney": {
        "header": ("session_id", "event_time", "source_id"),
        "files": (
            (
                "dt=2024-04-07/part-0001.csv",
                (
                    ("ses-3001", "2024-04-07T01:30:00", "edge-au"),
                    ("ses-3002", "2024-04-07T02:30:00", "edge-au"),
                    ("ses-3003", "2024-04-07T03:30:00", "edge-au"),
                ),
            ),
            (
                "dt=2024-10-06/part-0001.csv",
                (
                    ("ses-3004", "2024-10-06T01:30:00", "edge-au"),
                    ("ses-3005", "2024-10-06T02:30:00", "edge-au"),
                    ("ses-3006", "2024-10-06T03:30:00", "edge-au"),
                ),
            ),
            (
                "dt=2024-12-01/part-0001.csv",
                (
                    ("ses-3007", "2024-12-01T10:00:00", "edge-au"),
                ),
            ),
        ),
    },
    "clearing_debts": {
        "header": ("debt_id", "event_time", "source_id", "debtor", "creditor", "amount_cents", "priority"),
        "files": (("dt=2024-07-01/part-0001.csv", (
            ("ab", "2024-07-01T10:00:00Z", "ledger", "A", "B", "100", "2"),
            ("ac", "2024-07-01T10:00:00Z", "ledger", "A", "C", "100", "1"),
            ("ba", "2024-07-01T10:00:00Z", "ledger", "B", "A", "100", "1"),
            ("cb", "2024-07-01T10:00:00Z", "ledger", "C", "B", "50", "1"),
            ("de", "2024-07-01T10:00:00Z", "ledger", "D", "E", "30", "1"),
            ("ed", "2024-07-01T10:00:00Z", "ledger", "E", "D", "30", "1"),
        )),),
    },
}
