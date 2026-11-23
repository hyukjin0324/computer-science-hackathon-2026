"""자유 대화가 방금 화면의 말과 정확한 마감을 알고, 말투가 기계적이지 않게 (토큰은 아끼며)."""

import re
from pathlib import Path

from app import chat as chat_mod

HTML = (Path(__file__).resolve().parents[1] / "public" / "index.html").read_text(encoding="utf8")


def test_situation_includes_recent_lines_deadline_and_team_size():
    s = chat_mod.situation({"stage": "협업 중", "submit_days": 8, "submit_deadline": "12월 1일(화) 23:59", "members": 1, "notice": True,
                            "recent": [{"w": "b", "t": "팀원이 들어오면\n그때 나눠 드릴게요."}, {"w": "m", "t": "그때 나눠준다는게 무슨말이야"}]})
    assert "제출 마감 날짜 12월 1일(화) 23:59" in s and "팀원 1명" in s and "공고 연결됨" in s
    assert "[방금 화면] Bento: 팀원이 들어오면 그때 나눠 드릴게요. / 나: 그때 나눠준다는게 무슨말이야" in s


def test_situation_drops_bad_values_and_caps_length():
    s = chat_mod.situation({"stage": "협업 중", "submit_deadline": "<script>", "members": 999, "notice": "yes",
                            "recent": [{"w": "x", "t": "a"}] + [{"w": "b", "t": "가" * 500}] * 10})
    assert "<script>" not in s and "팀원" not in s and "공고 연결됨" not in s
    assert s.count("Bento:") == 4 and len(s) < 700  # 최대 4줄, 줄마다 120자


def test_prompt_no_longer_forces_press_the_button_ending():
    assert "버튼을 눌러 보라는 말로 맺는다" not in chat_mod.SYSTEM
    assert "[방금 화면]" in chat_mod.CONTEXT_RULE


def test_divide_requests_route_to_assign_but_questions_go_to_ai():
    rule = re.search(r"if \(/(나눠\|나누\|분배\|배정)/\.test\(n\) && !/(.+?)/\.test\(n\)\) return afterTasks", HTML)
    go, skip = re.compile(rule.group(1)), re.compile(rule.group(2))
    route = lambda t: bool(go.search(t.replace(" ", ""))) and not skip.search(t.replace(" ", ""))
    assert route("나중에 나눠줘") and route("할 일 분배해 줘")
    assert not route("그때 나눠준다는게 무슨말이야") and not route("나눠줘??") and not route("계획 나눠줘")
