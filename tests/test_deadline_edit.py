"""제출 마감을 나중에 고칠 수 있는지: GitHub '해커톤 제출' 마감과 숨겨 둔 공고 분석의 날짜를 함께 옮기는 제안."""

import pytest
from fastapi.testclient import TestClient

from app import main
from app import notice as nm
from app.notice import SAMPLE_NOTICE

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _fresh():
    main._demo.reset()
    main._EXECUTED.clear()
    nm._CACHE.clear()
    yield


def _with_submission_milestone():
    r = client.post("/api/notice", json={"text": SAMPLE_NOTICE}).json()
    assert client.post("/api/execute", json={"token": r["plan"]["token"]}).json()["ok"]
    return next(m for m in main._demo.d["milestones"] if m["title"] == "해커톤 제출")


def test_wrong_deadline_can_be_moved_on_github_with_teammate_approval():
    _with_submission_milestone()
    p = client.post("/api/notice/deadline", json={"submit_deadline_iso": "2026-12-15T18:00"}, headers={"X-Bento-User": "seoyeon"}).json()["proposal"]
    assert p["team_wide"] and "12월 15일" in p["steps"][0]["label"]
    me = client.post("/api/execute", json={"token": p["token"]}, headers={"X-Bento-User": "seoyeon"})
    assert me.status_code == 409 and me.json()["detail"]["need_second"]  # 제안한 사람 혼자서는 못 옮긴다
    assert client.post("/api/execute", json={"token": p["token"]}, headers={"X-Bento-User": "jiwoo"}).json()["ok"]
    ms = next(m for m in main._demo.d["milestones"] if m["title"] == "해커톤 제출")
    assert ms["due_on"].startswith("2026-12-15")
    saved = client.get("/api/notice/saved").json()["extract"]
    assert saved["submit_deadline_iso"] == "2026-12-15T18:00:00+09:00"  # 다른 기기·매일 점검이 읽는 값도 새 날짜
    assert nm.from_descriptions([ms["description"]]).title == saved["title"]  # 나머지 분석 결과는 그대로


def test_no_proposal_when_github_has_no_deadline_yet_or_same_date():
    r = client.post("/api/notice/deadline", json={"submit_deadline_iso": "2026-12-15T18:00"}).json()
    assert r["proposal"] is None and "이 기기에서만" in r["message"]
    ms = _with_submission_milestone()
    saved = nm.from_descriptions([ms["description"]]).submit_deadline_iso
    same = client.post("/api/notice/deadline", json={"submit_deadline_iso": saved}).json()
    assert same["proposal"] is None and "이미" in same["message"]


def test_bad_date_is_422():
    for v in ["내일", "2026-13-45", ""]:
        assert client.post("/api/notice/deadline", json={"submit_deadline_iso": v}).status_code == 422


def test_retime_description_keeps_everything_else():
    ms = _with_submission_milestone()
    out = nm.retime_description(ms["description"], "2027-01-05")
    assert out.startswith(ms["description"].split("<!--")[0])  # 사람이 읽는 앞부분은 그대로
    assert nm.from_descriptions([out]).submit_deadline_iso == "2027-01-05T23:59:00+09:00"
    assert nm.retime_description("설명만 있음", "2027-01-05") == "설명만 있음"
