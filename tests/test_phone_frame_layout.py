import re
from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "public" / "index.html").read_text(
    encoding="utf8"
)


def test_desktop_phone_frame_is_anchored_to_viewport_center():
    rule = re.search(r':root\[data-frame="on"\] \.phone \{([^}]+)\}', HTML)

    assert rule is not None
    declarations = rule.group(1)
    assert "position: absolute" in declarations
    assert "left: calc(50% - 12px)" in declarations
    assert "top: 50%" in declarations
    assert "transform: translate(-50%, -50%) scale(var(--phone-k, 1))" in declarations
    assert "transform-origin: center" in declarations
    assert "zoom:" not in declarations
