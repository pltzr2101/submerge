"""CSS regression tests — verify style.css layout rules."""

from __future__ import annotations

import re
from pathlib import Path


def _read_css() -> str:
    css_path = Path(__file__).parent.parent.parent / "src" / "submerge" / "static" / "style.css"
    return css_path.read_text(encoding="utf-8")


def _extract_rule(css: str, selector: str) -> str:
    """Extract all declaration blocks for a CSS selector as a single string.

    The *selector* is a regex-ready string (e.g. ``\\.action-menu\\.open``).
    Matches the selector regardless of whitespace before the opening brace.
    """
    pattern = re.compile(rf"{selector}\s*\{{(.*?)\}}", re.DOTALL)
    blocks = pattern.findall(css)
    return " ".join(blocks)


class TestActionMenuCSS:
    def test_action_menu_has_absolute_positioning(self):
        css = _read_css()
        block = _extract_rule(css, r"\.action-menu(?![\.\-\w])")
        # Base rule must declare a position (absolute for closed state; open overrides to fixed)
        assert "position:" in block or "position :" in block, (
            f".action-menu base rule must declare a position property, got: {block.strip()!r}"
        )
        assert "z-index:" in block or "z-index :" in block, (
            f".action-menu base rule must declare z-index, got: {block.strip()!r}"
        )

    def test_action_menu_begins_hidden_display_none(self):
        css = _read_css()
        block = _extract_rule(css, r"\.action-menu(?![\.\-\w])")
        # The base .action-menu must have display: none (hidden by default)
        assert "display: none" in block or "display:none" in block

    def test_action_menu_has_no_pointer_events_when_closed(self):
        css = _read_css()
        block = _extract_rule(css, r"\.action-menu(?![\.\-\w])")
        assert "pointer-events: none" in block or "pointer-events:none" in block

    def test_action_menu_open_shows_vertical_flex_column(self):
        css = _read_css()
        open_block = _extract_rule(css, r"\.action-menu\.open")
        assert open_block.strip(), (
            ".action-menu.open rule not found — expected display: flex; flex-direction: column"
        )
        open_block_no_comments = re.sub(r"/\*.*?\*/", "", open_block, flags=re.DOTALL)
        assert "display: flex" in open_block_no_comments, (
            f".action-menu.open must use display: flex, got: {open_block.strip()}"
        )
        assert "flex-direction: column" in open_block_no_comments, (
            f".action-menu.open must set flex-direction: column, got: {open_block.strip()}"
        )

    def test_action_menu_open_uses_fixed_positioning(self):
        css = _read_css()
        open_block = _extract_rule(css, r"\.action-menu\.open")
        assert "position: fixed" in open_block or "position:fixed" in open_block
        assert "z-index: 9999" in open_block or "z-index:9999" in open_block
        assert "pointer-events: all" in open_block or "pointer-events:all" in open_block

    def test_action_dropdown_has_isolation(self):
        css = _read_css()
        block = _extract_rule(css, r"\.action-dropdown(?![\.\-\w])")
        assert "isolation: isolate" in block

    def test_action_menu_item_is_block_level_full_width(self):
        css = _read_css()
        block = _extract_rule(css, r"\.action-menu-item(?![\.\-\w])")
        assert "width: 100%" in block or "width:100%" in block
        assert "text-align: left" in block or "text-align:left" in block
        # Must NOT be inline or inline-flex
        assert "display: inline" not in block
