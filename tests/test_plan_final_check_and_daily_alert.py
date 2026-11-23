"""계획의 '최종 점검' 시각, 매일 디스코드 알림이 제안 없는 일을 '승인하고 실행'으로 처리된다고 쓰지 않는지."""

from datetime import datetime, timedelta

import pytest

from app import main
from app.notice import SAMPLE_EXTRACT
from app.rules import KST
from app.schedule import work_plan


def test_final_check_is_three_hours_before_deadline_even_for_long_plans():
    now = datetime(2026, 11, 23, 10, 7, tzinfo=KST)
    due = datetime(2026, 12, 23, 23, 59, tzinfo=KST)  # 30일 뒤: 전에는 '12/23 01:57'처럼 어중간한 새벽 시각
    ex = SAMPLE_EXTRACT.model_copy(update={"apply_deadline_iso": None, "submit_deadline_iso": due.isoformat()})
    final = next(datetime.fromisoformat(s["due"]) for s in work_plan(ex, now) if s["title"] == "최종 점검")
    assert final == due - timedelta(hours=3)


@pytest.fixture
def sent(monkeypatch):
    out = []
    main._demo.reset()
    monkeypatch.setattr(main, "_post", lambda hook, text: (out.append(text), True)[1])
    monkeypatch.setattr(main.discord_mod, "read", lambda gh, key: "https://discord.example/hook")
    monkeypatch.setattr(main, "notice_watch", lambda gh, st, hook: "skip")
    return out


def _alert(sent):
    gh, st = main.state_for(None)
    main._agent_repo(main._demo.repo, gh, st)
    return next(t for t in sent if "처리를 기다리는 일" in t)


def test_daily_alert_says_items_without_proposals_need_a_person(sent):
    text = _alert(sent)
    assert "CI 실패" in text  # 데모의 PR #16: Bento가 대신 할 제안이 없다
    assert "제안이 없는 일은 앱에서 내용을 보고 직접 처리해 주세요" in text


def test_daily_alert_without_any_proposal_does_not_promise_approve_and_run(sent, monkeypatch):
    real = main.triage
    monkeypatch.setattr(main, "triage", lambda st, h: [{**it, "options": []} for it in real(st, h)])
    text = _alert(sent)
    assert "승인하고 실행" not in text and "직접 처리해 주세요" in text
