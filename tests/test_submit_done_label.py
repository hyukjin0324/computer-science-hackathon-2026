"""제출 완료 기록을 저장소에 남겨서 팀원 목록(/api/projects)에도 submitted로 보이는지, 다시 진행하면 풀리는지."""

from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def _submitted():
    return next(p for p in client.get("/api/projects").json()["projects"] if p["repo"] == main._demo.repo).get("submitted")


def test_submit_done_is_shared_and_reopen_clears_it():
    client.post("/api/demo/reset")
    assert not _submitted()
    assert client.post("/api/submit/done", json={}, headers={"X-Bento-User": "jiwoo"}).json()["ok"]
    assert _submitted()  # 다른 팀원 목록에서도 보관한 팀플로
    assert client.post("/api/submit/done", json={"done": False}, headers={"X-Bento-User": "doyoon"}).json()["ok"]
    assert not _submitted()
