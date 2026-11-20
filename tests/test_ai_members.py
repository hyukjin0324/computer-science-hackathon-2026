"""AI를 쓰는 기능은 Bento가 설치된 저장소의 팀원만 (팀 밖 GitHub 계정이 하루 AI 한도를 다 쓰지 못하게)."""

import base64
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_mod
from app import main
from app import notice as nm
from app.notice import SAMPLE_EXTRACT
from app.proposals import sign

client = TestClient(main.app)
NOTICE = "2026 AI 해커톤\n참가 신청: 2026년 12월 1일까지\n결과물 제출 마감: 2026년 12월 10일 18:00\n팀 구성: 2~4명"


class _FakeClaude:
    calls = 0

    def __init__(self, *a, **k):
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        _FakeClaude.calls += 1
        return SimpleNamespace(stop_reason="end_turn", model=kw["model"],
                               content=[SimpleNamespace(type="text", text=json.dumps(SAMPLE_EXTRACT.model_dump(), ensure_ascii=False))],
                               usage=SimpleNamespace(input_tokens=10, output_tokens=10))


@pytest.fixture(autouse=True)
def _live_login(monkeypatch):
    """로그인을 켠 실제 저장소 모드처럼. GitHub 대신 데모 저장소(팀원: jiwoo·seoyeon·doyoon·hajun)를 쓴다."""
    main._demo.reset()
    main._AI_MEMBER.clear()
    monkeypatch.setattr(main, "settings", main.settings.__class__(**{**main.settings.__dict__, "app_id": "1", "private_key": "k",
                                                                      "client_id": "Iv1.testclientid", "client_secret": "s" * 40}))
    monkeypatch.setattr(main, "gh_for", lambda repo: main._demo)
    monkeypatch.setattr(main, "installed_repos", lambda s: [main._demo.repo])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(nm.anthropic, "Anthropic", _FakeClaude)
    _FakeClaude.calls = 0
    nm._CACHE.clear()
    nm._ai_hits.clear()
    nm._ai_day.update(key="", n=0)
    yield
    main._STATE_CACHE.clear()
    main._AI_MEMBER.clear()


def _as(login):
    c = TestClient(main.app)
    c.cookies.set(auth_mod.SESSION, sign({"kind": "session", "login": login}, main.settings.signing_key))
    return c


def test_outsider_github_account_cannot_use_ai():
    out = _as("outsider")
    r = out.post("/api/notice", json={"text": NOTICE, "new_project": True})
    assert r.status_code == 403 and "팀원만" in r.json()["detail"] and _FakeClaude.calls == 0  # AI를 부르지 않는다
    assert out.post("/api/chat", json={"message": "안녕"}).status_code == 403
    img = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 100).decode()
    assert out.post("/api/notice/file", json={"name": "poster.png", "mime": "image/png", "data": img}).status_code == 403


def test_team_member_can_use_ai():
    r = _as("seoyeon").post("/api/notice", json={"text": NOTICE, "new_project": True})
    assert r.status_code == 200 and r.json()["source"] == "claude" and _FakeClaude.calls == 1


def test_outsider_still_gets_non_ai_reading():
    out = _as("outsider")
    assert out.post("/api/notice", json={"text": NOTICE, "new_project": True, "no_ai": True}).json()["source"] == "rules"  # 규칙으로 읽기는 그대로
    _as("seoyeon").post("/api/notice", json={"text": NOTICE, "new_project": True})  # 팀원이 한 번 AI로 읽어 두면
    assert out.post("/api/notice", json={"text": NOTICE, "new_project": True}).json()["source"] == "cache"  # 같은 공고는 AI 없이 저장된 결과
    assert _FakeClaude.calls == 1


def test_membership_is_remembered_briefly(monkeypatch):
    calls = []
    real = main._demo.members
    monkeypatch.setattr(main._demo, "members", lambda: calls.append(1) or real())
    for _ in range(3):
        _as("outsider").post("/api/chat", json={"message": "안녕"})
    assert len(calls) == 1  # 매번 GitHub에 묻지 않는다


def test_demo_and_local_mode_unchanged(monkeypatch):
    monkeypatch.setattr(main, "settings", main.settings.__class__(**{**main.settings.__dict__, "app_id": None, "private_key": None,
                                                                      "client_id": None, "client_secret": None}))
    r = client.post("/api/notice", json={"text": NOTICE, "new_project": True})
    assert r.status_code == 200 and r.json()["source"] == "claude"  # 로그인이 꺼진 데모·로컬은 그대로
