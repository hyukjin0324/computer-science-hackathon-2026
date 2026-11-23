"""이미 대회가 연결된 저장소에 다른 대회 공고를 연결하면 대회를 바꾸는 제안이 나오는지 (전에는 제안이 없어 옛 대회가 그대로 남았다)."""

from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)
A = "A 해커톤 공고\n참가 신청: 2026.12.01까지\n결과물 제출: 2026.12.10 23:59까지\n제출물: 발표자료, 시연 영상"
B = "B 공모전 공고\n접수 마감: 2026.12.20까지\n최종 제출: 2026.12.28 23:59까지\n제출물: 기획서, 데모 영상"


def _link(text):
    return client.post("/api/notice", json={"text": text, "no_ai": True}).json()


def test_linking_another_contest_offers_a_swap_and_closes_stale_deliverables():
    client.post("/api/demo/reset")  # 데모 저장소와 '이미 실행한 승인' 기록을 함께 비운다
    a = _link(A)
    assert client.post("/api/execute", json={"token": a["plan"]["token"]}).json()["ok"]
    b = _link(B)
    assert b["plan"] and b["replaces"] == "A 해커톤 공고"
    labels = [s["label"] for s in b["plan"]["steps"]]
    assert labels[0].startswith("'해커톤 제출' 마감일을 12월 28일")
    assert any(l.startswith("할 일 추가: [제출물] 기획서") for l in labels)
    assert any(l.endswith("닫기") for l in labels)  # A에만 있던 '[제출물] 발표자료'
    r = client.post("/api/execute", json={"token": b["plan"]["token"]}, headers={"X-Bento-User": "jiwoo"}).json()
    assert r["ok"]
    assert client.get("/api/notice/saved").json()["extract"]["title"] == "B 공모전 공고"


def test_same_contest_again_offers_nothing():
    client.post("/api/demo/reset")  # 데모 저장소와 '이미 실행한 승인' 기록을 함께 비운다
    a = _link(A)
    assert client.post("/api/execute", json={"token": a["plan"]["token"]}).json()["ok"]
    assert not _link(A).get("plan")


def test_manual_deadline_label_is_korea_time():
    client.post("/api/demo/reset")
    r = client.post("/api/notice", json={"text": A, "no_ai": True, "submit_deadline_iso": "2026-12-28T14:59:00.000Z"}).json()
    assert r["extract"]["submit_deadline"].endswith("23:59")  # 화면이 보낸 UTC를 그대로 적으면 14:59가 됐다


def test_replanning_moves_existing_plan_steps_and_swap_keeps_plan_issues_in_place():
    client.post("/api/demo/reset")
    a = _link(A)
    assert client.post("/api/execute", json={"token": a["plan"]["token"]}).json()["ok"]
    steps = [{"title": "시연 영상 촬영", "due": "2026-12-08T23:00:00+09:00"}]
    p = client.post("/api/schedule/github", json={"steps": steps}).json()["proposal"]
    assert client.post("/api/execute", json={"token": p["token"]}).json()["ok"]
    b = _link(B)
    assert not any("[계획]" in s["label"] for s in b["plan"]["steps"])  # '[계획] 시연 영상 촬영'을 제출물로 끌어오지 않는다
    moved = client.post("/api/schedule/github", json={"steps": [{"title": "시연 영상 촬영", "due": "2026-12-26T23:00:00+09:00"}]}).json()["proposal"]
    assert moved and any("마감일을 12월 26일" in s["label"] for s in moved["steps"])  # 같은 단계는 새 날짜로 옮긴다
