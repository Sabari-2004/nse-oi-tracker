from datetime import date

from collector.participant_oi import parse_participant_oi_csv
from database.repository import SignalRepository


REPORT = """Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Sep 10,2026
Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short,Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,Total Long Contracts,Total Short Contracts
Client,100,110,200,180,1,2,3,4,5,6,7,8,314,320
FII,"1,000",900,300,350,1,2,3,4,5,6,7,8,1314,1280
TOTAL,1100,1010,500,530,2,4,6,8,10,12,14,16,1628,1600
"""


def test_participant_oi_parser_locates_header_and_retains_eod_date():
    rows = parse_participant_oi_csv(REPORT, date(2026, 9, 10))
    assert [row["participant"] for row in rows] == ["CLIENT", "FII", "TOTAL"]
    assert rows[1]["net_index_futures"] == 100
    assert rows[1]["measures"]["Future Index Long"] == 1000
    assert rows[1]["report_date"] == "2026-09-10"


def test_participant_oi_repository_returns_latest_complete_report(tmp_path):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    repository.upsert_participant_oi(parse_participant_oi_csv(REPORT, date(2026, 9, 10)))
    latest = repository.latest_participant_oi()
    assert latest["report_date"] == "2026-09-10"
    assert latest["participants"][1]["participant"] == "FII"
