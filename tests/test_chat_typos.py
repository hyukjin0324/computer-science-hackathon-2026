"""대화 낱말 규칙이 자주 나는 오타('헤커톤', '공모저')도 알아듣고, 진짜 낱말은 건드리지 않는지."""

import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "public" / "index.html").read_text(encoding="utf8")
BLOCK = re.search(r"const TYPOS = \[(.+?)\];", HTML, re.S).group(1)
RULES = [(re.compile(rx), to) for rx, to in re.findall(r'\[/(.+?)/g, "(.+?)"\]', BLOCK)]


def fix(text):
    n = re.sub(r"\s", "", text)
    for rx, to in RULES:
        n = rx.sub(to, n)
    return n


def test_common_typos_are_fixed():
    assert "해커톤" in fix("헤커톤 바꿔줘") and "해커톤" in fix("해카톤 찾아줘")
    assert "공모전" in fix("공모저 찾아줘") and "공모전" in fix("공모젼 추천")
    assert "제출" in fix("재출 점검해줘") and "계획" in fix("게획 짜줘") and "마감" in fix("마깜 언제야")
    assert "바꿔" in fix("대회 바꿰줘")


def test_real_words_are_left_alone():
    for word in ("대외활동", "공무원", "일장연설", "공구"):
        assert fix(word) == word, word
