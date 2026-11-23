"""팀장 넘기기·되찾기: 지금 팀장은 넘기고, 저장소 관리자는 되찾고, 다른 사람은 못 바꾼다."""

from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)
H = lambda u: {"X-Bento-User": u}


def _lead():
    return client.get("/api/roles").json()["lead"]


def test_current_lead_hands_over_and_admin_reclaims():
    client.post("/api/demo/reset")
    assert client.get("/api/roles").json()["admins"] == ["jiwoo"]
    assert client.post("/api/roles", json={"roles": {"seoyeon": ["fe", "lead"]}}, headers=H("seoyeon")).status_code == 200
    assert _lead() == "seoyeon"
    assert client.post("/api/roles/lead", json={"to": "doyoon"}, headers=H("hajun")).status_code == 403  # 팀장 아님
    assert client.post("/api/roles/lead", json={"to": "doyoon"}, headers=H("seoyeon")).json()["saved"]
    roles = client.get("/api/roles").json()
    assert roles["lead"] == "doyoon" and "lead" not in roles["roles"]["seoyeon"]  # 팀장은 한 명
    assert client.post("/api/roles/lead", json={"to": "jiwoo"}, headers=H("jiwoo")).json()["saved"]  # 관리자가 되찾기
    assert _lead() == "jiwoo"


def test_admin_cannot_push_lead_onto_someone_else_and_outsiders_are_rejected():
    client.post("/api/demo/reset")
    client.post("/api/roles", json={"roles": {"seoyeon": ["fe", "lead"]}}, headers=H("seoyeon"))
    assert client.post("/api/roles/lead", json={"to": "hajun"}, headers=H("jiwoo")).status_code == 403  # 관리자는 자기에게만 되찾는다
    assert client.post("/api/roles/lead", json={"to": "outsider"}, headers=H("seoyeon")).status_code == 400
