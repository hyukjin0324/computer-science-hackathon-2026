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


def test_lazy_tick_needs_login_or_code(monkeypatch):
    """'반대 없으면 실행'은 GitHub에 댓글을 달거나 실행하므로 아무나 부르지 못한다."""
    monkeypatch.setattr(main, "settings", main.settings.__class__(**{**main.settings.__dict__, "access_code": "c0de"}))
    assert client.post("/api/lazy/tick", json={}).status_code == 401
    assert client.post("/api/lazy/tick", json={}, headers={"X-Bento-Code": "c0de"}).status_code == 200


def test_fetch_stops_when_actual_connection_is_internal():
    """검사 때와 연결 때 DNS 답이 다르면(DNS 바꿔치기) 실제로 연결된 서버 IP가 내부망인지 다시 보고 멈춘다."""
    from types import SimpleNamespace

    from app.fetch import FetchError, _check_peer

    def resp(ip):
        return SimpleNamespace(extensions={"network_stream": SimpleNamespace(get_extra_info=lambda k: (ip, 443) if k == "server_addr" else None)})
    for bad in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"):
        with pytest.raises(FetchError):
            _check_peer(resp(bad))
    _check_peer(resp("8.8.8.8"))  # 바깥 주소는 그대로
    _check_peer(SimpleNamespace(extensions={}))  # 연결 정보를 알 수 없으면 넘어간다
