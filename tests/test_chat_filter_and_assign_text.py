"""잡담 거르기가 대회 준비 말을 막지 않는지, 담당자 나누기 카드의 설명이 버튼과 맞는지."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)
HTML = (Path(__file__).resolve().parents[1] / "public" / "index.html").read_text(encoding="utf8")


def _rx(name):
    m = re.search(rf"const {name} = /(.+?)/(i?);", HTML)
    return re.compile(m.group(1), re.I if m.group(2) else 0)


def _offtopic(text):
    n = re.sub(r"\s", "", text)
    return bool(_rx("OFFTOPIC_RX").search(n)) and not _rx("ONTOPIC_RX").search(n)


def test_contest_prep_words_are_not_treated_as_small_talk():
    for t in ("뉴스 요약 서비스로 낼 아이디어 추천해줘", "발표 영상 배경 노래 추천해줘", "영화 추천 앱 주제 어때", "시연 영상에 넣을 노래"):
        assert not _offtopic(t), t


def test_plain_small_talk_is_still_blocked():
    for t in ("오늘 날씨 어때?", "비트코인 오를까", "심심해"):
        assert _offtopic(t), t


def test_assign_card_text_matches_its_button():
    main._demo.reset()
    it = next(i for i in client.get("/api/triage").json()["items"] if i["id"] == "assign")
    label = it["options"][0]["label"]
    assert ("역할에 맞게" in it["why"]) == (label == "역할대로 나누기")


def test_assign_card_without_roles_does_not_promise_role_split():
    import dataclasses
    from app.triage import triage
    main._demo.reset()
    _, st = main.state_for(None)
    it = next(i for i in triage(dataclasses.replace(st, roles={})) if i["id"] == "assign")
    assert it["options"][0]["label"] == "맡은 일이 적은 순서로 나누기"
    assert "역할에 맞게" not in it["why"] and "맡은 일이 적은 사람부터" in it["why"]


def _match(name, text, spaces=False):
    return bool(_rx(name).search(text if spaces else re.sub(r"\s", "", text)))


def test_who_questions_about_issues_are_not_bot_intro():
    for t in ("#11 이슈 담당 누구야?", "PR 14 리뷰어 누구야", "발표 누구세요"):
        assert not _match("ABOUT_BOT_RX", t), t
    for t in ("누구야?", "누구세요", "너 누구야", "Bento는 누구야", "너 이름이 뭐야"):
        assert _match("ABOUT_BOT_RX", t), t


def test_topic_name_is_not_my_name():
    for t in ("프로젝트 주제 이름 추천해줘", "과제 역할 나눠줘", "주제 이름 뭐로 하지"):
        assert not _match("ABOUT_ME_RX", t, spaces=True), t
    for t in ("내 이름 뭐야", "내이름 뭐야", "나는 누구야", "이번 대회에서 내 역할 뭐야", "제 역할이 뭐예요"):
        assert _match("ABOUT_ME_RX", t, spaces=True), t


def test_quiet_lead_card_goes_to_another_active_member():
    from datetime import datetime, timedelta, timezone
    from app.demo import DemoGitHub
    from app.state import build_state
    from app.watch import watch
    gh = DemoGitHub()
    old = (datetime.now(timezone.utc) - timedelta(hours=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for e in gh.d["events"]:
        if e["actor"]["login"] == "jiwoo":
            e["created_at"] = old
    for c in gh.d["commits"]:
        if (c.get("author") or {}).get("login") == "jiwoo":
            c["commit"]["author"]["date"] = c["commit"]["committer"]["date"] = old
    gh.d["issues"][0]["assignees"].append({"login": "jiwoo"})  # 팀장도 맡은 일이 있다
    st = build_state(gh)
    w = {it["id"]: it for it in watch(st)}
    assert "quiet-jiwoo" in w
    assert w["quiet-jiwoo"]["who"] in st.members and w["quiet-jiwoo"]["who"] != "jiwoo"
