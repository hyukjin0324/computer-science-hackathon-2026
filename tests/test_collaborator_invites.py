from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import SESSION
from app.demo import DemoGitHub
from app.github import GitHub, GitHubError
from app.proposals import sign


@pytest.fixture
def invite_repo(monkeypatch):
    repo = DemoGitHub()
    monkeypatch.setattr(main, "gh_for", lambda _: repo)
    monkeypatch.setattr(main, "_EXECUTED", {})
    return repo


def test_demo_invitation_requires_approval_and_does_not_duplicate(invite_repo):
    with TestClient(main.app) as client:
        def propose(login):
            return client.post("/api/collaborator-invite", json={"repo": invite_repo.repo, "login": login})

        assert propose("https://github.com/octocat").status_code == 422
        token = propose(" @Octo-Cat ").json()["proposal"]["token"]
        assert invite_repo.log == []
        result = client.post("/api/execute", json={"token": token}).json()
        assert result["ok"] and invite_repo.log == ["invite octo-cat"]
        assert client.post("/api/execute", json={"token": token}).status_code == 409
        for existing in ("MINJI", "JIWOO"):
            token = propose(existing).json()["proposal"]["token"]
            result = client.post("/api/execute", json={"token": token}).json()
            assert result["ok"] and result["results"][0]["skipped"]
        assert invite_repo.log == ["invite octo-cat"]


def test_batch_invitation_retries_only_the_unsent_recipients(monkeypatch, invite_repo):
    send = invite_repo.add_collaborator
    unavailable = {"middle"}

    def send_invitation(login):
        if login in unavailable:
            raise GitHubError("temporary failure", 422)
        return send(login)

    monkeypatch.setattr(invite_repo, "add_collaborator", send_invitation)
    with TestClient(main.app) as client:
        body = {"repo": invite_repo.repo, "logins": ["First", "middle", "last", " @FIRST "]}
        proposal = client.post("/api/collaborator-invite", json=body).json()["proposal"]
        assert len(proposal["steps"]) == 3 and invite_repo.log == []
        request = {"token": proposal["token"]}
        initial = client.post("/api/execute", json=request).json()
        assert [r["ok"] for r in initial["results"]] == [True, False, True]
        assert invite_repo.log == ["invite first", "invite last"]

        unavailable.clear()
        retry = client.post("/api/execute", json=request).json()
        assert retry["ok"]
        assert [r.get("skipped", False) for r in retry["results"]] == [True, False, True]
        assert invite_repo.log == ["invite first", "invite last", "invite middle"]


def test_only_authenticated_repository_admin_can_send_live_invitation(monkeypatch, invite_repo):
    settings = replace(main.settings, app_id="1", private_key="mock", client_id="id",
                       client_secret="mock", signing_key="test-key")
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(invite_repo, "collaborator_permission",
                        lambda login: {"jiwoo": "admin", "seoyeon": "write"}.get(login, "none"), raising=False)

    def login(client, name):
        client.cookies.set(SESSION, sign({"kind": "session", "login": name}, settings.signing_key))

    with TestClient(main.app) as client:
        body = {"repo": invite_repo.repo, "logins": ["outside-bento", "another-teammate"]}
        assert client.post("/api/collaborator-invite", json=body, headers={"X-Bento-User": "jiwoo"}).status_code == 401
        login(client, "outsider")
        assert client.post("/api/collaborator-invite", json=body).status_code == 403
        login(client, "seoyeon")
        approval = {"token": client.post("/api/collaborator-invite", json=body).json()["proposal"]["token"]}
        denied = client.post("/api/execute", json=approval, headers={"X-Bento-User": "jiwoo"})
        assert denied.status_code == 409 and denied.json()["detail"]["need_second"]
        request = client.post("/api/approval-request", json=approval)
        assert request.status_code == 200
        assert not any(entry.startswith("invite ") for entry in invite_repo.log)
        login(client, "outsider")
        assert client.post("/api/execute", json=approval).status_code == 403
        login(client, "jiwoo")
        result = client.post("/api/execute", json=approval).json()
        assert result["ok"] and "GitHub에서 수락" in result["results"][0]["note"]
        assert invite_repo.log.count("invite outside-bento") == 1
        assert invite_repo.log.count("invite another-teammate") == 1
        assert invite_repo.issue(request.json()["issue"])["state"] == "closed"


def test_github_invite_uses_installation_token_and_default_permission(monkeypatch):
    def handler(request):
        assert request.method == "PUT"
        assert request.url.path == "/repos/team/project/collaborators/octocat"
        assert request.headers["Authorization"] == "Bearer installation-token"
        assert request.headers["Content-Length"] == "0"
        return httpx.Response(201, json={"id": 42})

    with httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler)) as http:
        gh = GitHub(main.settings, "team/project", http=http)
        monkeypatch.setattr(gh, "_installation_token", lambda: "installation-token")
        assert gh.add_collaborator("octocat") == {"id": 42}
