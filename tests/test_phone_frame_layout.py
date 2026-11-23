import re
from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "public" / "index.html").read_text(
    encoding="utf8"
)


def test_desktop_phone_frame_scales_around_its_center():
    rule = re.search(r':root\[data-frame="on"\] \.phone \{([^}]+)\}', HTML)

    assert rule is not None
    declarations = rule.group(1)
    assert "transform: scale(var(--phone-k, 1))" in declarations
    assert "transform-origin: center" in declarations
    assert "zoom:" not in declarations
