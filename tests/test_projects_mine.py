"""팀플 고르기: GitHub로 로그인했으면 내가 팀원인 저장소만 보여 주는지 (배포한 사람의 저장소만 뜨던 것)."""

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_mod
from app import main
from app.proposals import sign

client = TestClient(main.app)


class _OtherRepo:
    repo = "deployer/their-repo"

    def members(self):
        return ["deployer"]

    def milestones(self):
        return []


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    main._demo.reset()
    monkeypatch.setattr(main, "settings", main.settings.__class__(**{**main.settings.__dict__, "app_id": "1", "private_key": "k",
                                                                      "client_id": "Iv1.testclientid", "client_secret": "s" * 40,
                                                                      "default_repo": "deployer/their-repo"}))
    monkeypatch.setattr(main, "installed_repos", lambda s: ["deployer/their-repo", main._demo.repo])
    monkeypatch.setattr(main, "gh_for", lambda repo: _OtherRepo() if repo == "deployer/their-repo" else main._demo)
    yield


def _as(login):
    c = TestClient(main.app)
    c.cookies.set(auth_mod.SESSION, sign({"kind": "session", "login": login}, main.settings.signing_key))
    return c


def test_logged_in_user_sees_only_their_repos_and_default_moves_to_theirs():
    r = _as("seoyeon").get("/api/projects").json()  # seoyeon은 데모 저장소(knu-matzip) 팀원
    assert [p["repo"] for p in r["projects"]] == [main._demo.repo]
    assert r["default"] == main._demo.repo and r["mine_only"]  # 서버 기본 저장소의 팀원이 아니니 내 저장소가 기본


def test_user_with_no_installed_repo_gets_empty_list():
    r = _as("newcomer").get("/api/projects").json()
    assert r["projects"] == [] and r["default"] is None


def test_deployer_still_sees_their_repo_as_default():
    r = _as("deployer").get("/api/projects").json()
    assert [p["repo"] for p in r["projects"]] == ["deployer/their-repo"] and r["default"] == "deployer/their-repo"


def test_not_logged_in_sees_no_ones_repos():
    r = client.get("/api/projects").json()
    assert r["projects"] == []  # 로그인 전에는 배포한 사람의 저장소도 보여 주지 않는다


def test_demo_and_no_login_server_unchanged(monkeypatch):
    monkeypatch.setattr(main, "settings", main.settings.__class__(**{**main.settings.__dict__, "client_id": None, "client_secret": None}))
    r = client.get("/api/projects").json()  # 로그인을 켜지 않은 서버(팀 접근 코드 방식)는 지금처럼 전부
    assert len(r["projects"]) == 2
