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
