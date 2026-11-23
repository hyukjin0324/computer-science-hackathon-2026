"""제출 완료를 기록하면(확인 이슈를 증거와 함께 닫음) 다른 팀원도 /api/submit/status로 알 수 있는지."""

from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def test_status_finds_closed_confirm_issue_with_proof():
    client.post("/api/demo/reset")
    head = main._demo.d["head"]
    p = client.post("/api/submit/confirm", json={"title": "2026 해커톤", "head_sha": head}).json()["proposal"]
    assert client.post("/api/execute", json={"token": p["token"]}).json()["ok"]
    n = client.get("/api/submit/status").json()["issue"]
    proof = client.post("/api/submit/proof", json={"issue": n, "proof": "접수번호 HK-0042"}).json()["proposal"]
    assert client.post("/api/execute", json={"token": proof["token"]}).json()["ok"]
    s = client.get("/api/submit/status").json()  # 이슈 번호 없이 (다른 팀원 화면)
    assert s["issue"] == n and s["closed"] and s["proof"] == "접수번호 HK-0042"
