"""매일 자동 알림: 서버가 바뀌어도 같은 알림을 다시 보내지 않는지, 저장소 하나가 실패해도 나머지를 점검하는지."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)
HOOK = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture(autouse=True)
def _fresh():
    main._demo.reset()
    main._EXECUTED.clear()
    yield
    main._STATE_CACHE.clear()


def _plan_milestone(n, title, hours_from_now):
    due = (datetime.now(timezone.utc) + timedelta(hours=hours_from_now)).strftime("%Y-%m-%dT%H:%M:%SZ")
    main._demo.d["milestones"].append({"number": n, "title": title, "due_on": due, "description": "", "html_url": f"https://github.com/x/y/milestone/{n}"})
    return {"number": n, "title": title}


def _late_step():
    ms = _plan_milestone(7, "계획 · 핵심 기능", -30)  # 30시간 전에 끝났어야 하는 단계
    main._demo.create_issue("[계획] 핵심 기능", "", [], ms["number"])


def _cron(monkeypatch, status=200):
    import httpx
    from app import discord as dm
    monkeypatch.setenv("CRON_SECRET", "s3")
    monkeypatch.delenv("BENTO_AUTO_NOTIFY", raising=False)
    sent = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: sent.append(kw["json"]["content"]) or SimpleNamespace(status_code=status))
    dm.write(main._demo, HOOK, main.settings.signing_key)  # 팀이 디스코드를 직접 연결 = 알림을 받겠다는 뜻
    return sent


def _run_cron():
    main._LAST_ALERT.clear()  # 매번 새 서버(서버리스 인스턴스)라고 치기: 메모리는 비어 있다
    return client.get("/api/agent/run", headers={"Authorization": "Bearer s3"}).json()


def test_daily_alerts_are_not_repeated_on_a_new_server(monkeypatch):
    sent = _cron(monkeypatch)
    _late_step()
    _run_cron()
    first = list(sent)
    assert any("늦었어요" in m for m in first) and any("처리를 기다리는 일" in m for m in first)
    _run_cron()  # 다음 날, 다른 서버
    assert sent == first  # 같은 발견·같은 단계의 처리 대기는 다시 보내지 않는다 (전에는 서버 메모리로만 기억해서 매일 다시 갔다)
    labels = {lb["name"] for lb in main._demo.labels()}
    assert {"bento-alerted-sum", "bento-alerted-esc"} <= labels


def test_daily_alert_is_sent_again_when_discord_failed(monkeypatch):
    sent = _cron(monkeypatch, status=500)  # 디스코드가 잠깐 안 됨
    _late_step()
    r = _run_cron()
    assert r["report"][0]["notified"] is False and "error" not in r["report"][0]  # 실패해도 그 저장소의 다른 점검은 끝까지
    tried = sum("늦었어요" in m for m in sent)
    import httpx
    monkeypatch.setattr(httpx, "post", lambda url, **kw: sent.append(kw["json"]["content"]) or SimpleNamespace(status_code=204))
    assert _run_cron()["report"][0]["notified"] is True  # 보낸 것으로 표시하지 않았으니 다음 실행 때 다시 보낸다
    assert sum("늦었어요" in m for m in sent) == tried + 1


def test_one_broken_repo_does_not_stop_the_daily_check_for_others(monkeypatch):
    _cron(monkeypatch)
    monkeypatch.setattr(main, "settings", main.settings.__class__(**{**main.settings.__dict__, "app_id": "1", "private_key": "k"}))
    monkeypatch.setattr(main, "installed_repos", lambda s: ["team/broken", main._demo.repo])

    def gh_for(repo):
        if repo == "team/broken":
            raise RuntimeError("이 저장소만 읽다가 오류")
        return main._demo
    monkeypatch.setattr(main, "gh_for", gh_for)
    _late_step()
    rep = _run_cron()["report"]
    assert rep[0] == {"repo": "team/broken", "error": True}
    assert rep[1]["repo"] == main._demo.repo and rep[1]["notified"] is True  # 뒤 저장소도 점검하고 알린다
