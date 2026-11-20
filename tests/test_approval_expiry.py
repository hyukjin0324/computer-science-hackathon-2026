"""다른 팀원에게 보낸 승인 요청은 3일 동안 승인할 수 있고, 지나면 '오래된 승인 요청'으로 남는지."""

import time

import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _fresh():
    main._demo.reset()
    main._EXECUTED.clear()
    yield


def _later(monkeypatch, hours):
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + hours * 3600)


def _request():
    """seoyeon이 팀 규칙 바꾸기를 제안하고 승인 요청을 보낸다."""
    p = client.post("/api/team-rules", json={"lazy": ["reviewers"], "hours": 12}, headers={"X-Bento-User": "seoyeon"}).json()["proposal"]
    r = client.post("/api/approval-request", json={"token": p["token"]}).json()
    return p["token"], r["issue"]


def _queue():
    return [t for t in client.get("/api/triage").json()["items"] if t["id"].startswith("approve-")]


def test_request_stays_and_can_be_approved_after_six_hours(monkeypatch):
    token, n = _request()
    _later(monkeypatch, 10)  # 밤에 보내고 아침에 확인
    q = _queue()
    assert len(q) == 1 and q[0]["proposal"] and not q[0].get("expired")  # 전에는 6시간이 지나면 목록에서 사라졌다
    r = client.post("/api/execute", json={"token": q[0]["proposal"]["token"]}, headers={"X-Bento-User": "jiwoo"})
    assert r.status_code == 200 and r.json()["ok"], r.text[:200]
    assert all(i["number"] != n or i.get("state") == "closed" for i in main._demo.d["issues"])  # 실행되면 요청 이슈도 닫힌다


def test_request_older_than_three_days_is_shown_as_expired_and_cannot_run(monkeypatch):
    token, n = _request()
    _later(monkeypatch, 24 * 4)
    q = _queue()
    assert len(q) == 1 and q[0].get("expired") and "proposal" not in q[0] and "오래된 승인 요청" in q[0]["title"]
    assert q[0]["handoff"] == [] and q[0]["evidence"][0]["label"] == f"#{n} 승인 요청"  # 화면이 쓰는 칸이 모두 있다
    r = client.post("/api/execute", json={"token": token}, headers={"X-Bento-User": "jiwoo"})
    assert r.status_code == 400


def test_normal_proposals_still_expire_after_six_hours(monkeypatch):
    p = client.post("/api/team-rules", json={"lazy": ["reviewers"], "hours": 12}, headers={"X-Bento-User": "seoyeon"}).json()["proposal"]
    _later(monkeypatch, 7)  # 승인 요청으로 올리지 않은 보통 제안
    r = client.post("/api/execute", json={"token": p["token"]}, headers={"X-Bento-User": "jiwoo"})
    assert r.status_code == 400 and "오래된 제안" in r.json()["detail"]
