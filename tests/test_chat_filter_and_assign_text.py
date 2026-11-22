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
