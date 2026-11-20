"""제출 점검이 사용자가 적은 링크를 열 때 로그인·팀 접근 코드를 확인하는지."""

import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _fresh():
    main._demo.reset()
    main._EXECUTED.clear()
    yield


def test_submit_check_opens_typed_links_only_with_login_or_code(monkeypatch):
    """제출 점검이 사용자가 적은 링크를 서버가 대신 열어 보는 것: 공고 링크 열기처럼 로그인·팀 접근 코드가 있어야 한다."""
    import app.submission as sm
    opened = []
    monkeypatch.setattr(sm, "probe", lambda url: opened.append(url) or {"ok": True, "status": 200, "url": url, "error": None})
    monkeypatch.setattr(main, "settings", main.settings.__class__(**{**main.settings.__dict__, "access_code": "c0de"}))
    body = {"deliverables": [{"name": "배포 링크", "quote": "배포 링크"}], "links": {"deploy": "https://example.com/app", "video": "https://example.com/v"}}
    r = client.post("/api/submit-check", json=body)
    assert r.status_code == 401 and opened == []  # 코드 없이는 남의 주소를 열지 않는다
    assert client.post("/api/submit-check", json=body, headers={"X-Bento-Code": "c0de"}).status_code == 200 and opened
    assert client.post("/api/submit-check", json={"deliverables": []}).status_code == 200  # 링크 없는 점검은 그대로
