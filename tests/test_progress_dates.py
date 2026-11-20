"""진행 점검이 시간대 없는 날짜를 받아도 서버 오류가 나지 않는지."""

import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _fresh():
    main._demo.reset()
    main._EXECUTED.clear()
    yield


@pytest.mark.parametrize("due,deadline,start", [
    ("2026-12-01", "2026-12-10", None),                                         # 날짜만
    ("2026-12-01T00:00:00", "2026-12-10T18:00:00+09:00", None),                 # 시간대 있는 것과 없는 것이 섞임
    ("2026-12-01T00:00:00+09:00", "2026-12-10T18:00:00+09:00", "2026-11-15T00:00:00"),
])
def test_progress_accepts_dates_without_timezone(due, deadline, start):
    r = client.post("/api/progress", json={"plan": [{"title": "기획", "due": due}], "deadline_iso": deadline, "plan_start_iso": start})
    assert r.status_code == 200, r.text[:200]  # 전에는 500


def test_progress_bad_date_is_400_not_500():
    r = client.post("/api/progress", json={"plan": [{"title": "기획", "due": "내일"}], "deadline_iso": "2026-12-10"})
    assert r.status_code == 400


@pytest.mark.parametrize("bad", [" ", "abc", "2026-13-45"])
def test_submit_check_bad_deadline_is_a_warning_not_500(bad):
    r = client.post("/api/submit-check", json={"deliverables": [], "deadline_iso": bad})
    assert r.status_code == 200, r.text[:200]  # 전에는 500
    row = next(x for x in r.json()["rows"] if x["name"] == "마감까지 남은 시간")
    assert row["status"] == "warn" and "읽지 못했어요" in row["detail"]


@pytest.mark.parametrize("line,expect", [("팀 구성: 2명 ~ 4명", (2, 4)), ("팀 구성: 2인~4인", (2, 4)), ("팀 구성: 2~4명", (2, 4))])
def test_team_size_with_unit_on_both_sides(line, expect):
    from app.rules import rule_extract
    ex = rule_extract("2026 해커톤\n" + line)
    assert (ex.team_min, ex.team_max) == expect


def test_eligibility_reads_team_size_with_unit_on_both_sides():
    from app.eligibility import rules
    from app.notice import Fact, NoticeExtract
    q = "2명 ~ 4명으로 팀 구성"
    r = rules(NoticeExtract(title="t", facts=[Fact(label="참가 자격", value=q, quote=q)]))
    assert (r["team"]["min"], r["team"]["max"]) == (2, 4)
