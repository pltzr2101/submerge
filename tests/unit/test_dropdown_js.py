"""Regression tests for dropdown JavaScript behaviour in index.html."""

from __future__ import annotations

import re
from pathlib import Path


def _read_index_html() -> str:
    html_path = (
        Path(__file__).parent.parent.parent / "src" / "submerge" / "templates" / "index.html"
    )
    return html_path.read_text(encoding="utf-8")


def _extract_toggle_body(html: str) -> str:
    """Extract everything between 'function toggleActionMenu' and the next top-level
    'function ' or '// Close menus on scroll'."""
    m = re.search(r"function toggleActionMenu\(.*?\)\s*\{(.*?)\n\}", html, re.DOTALL)
    return m.group(1) if m else ""


class TestDropdownJS:
    def test_stop_propagation_present(self):
        html = _read_index_html()
        body = _extract_toggle_body(html)
        assert "stopPropagation" in body, (
            "toggleActionMenu must call event.stopPropagation() to prevent "
            "the click-outside listener from closing the menu immediately"
        )

    def test_menu_opened_at_guard_present(self):
        html = _read_index_html()
        assert "_menuOpenedAt" in html, (
            "Script must define _menuOpenedAt guard variable for scroll debounce"
        )

    def test_scroll_listener_has_debounce_guard(self):
        html = _read_index_html()
        # The scroll/resize close-all listener must reference _menuOpenedAt
        # The pattern uses forEach with an arrow function on window.addEventListener
        m = re.search(
            r"_menuOpenedAt.*?addEventListener\s*\(.*?(?:scroll|resize|evt)",
            html,
            re.DOTALL,
        )
        assert m is not None, "scroll/resize listeners must check _menuOpenedAt for debounce guard"

    def test_vertical_clamp_present(self):
        html = _read_index_html()
        body = _extract_toggle_body(html)
        assert "viewportHeight" in body or "innerHeight" in body, (
            "toggleActionMenu must compute viewport height for vertical clamping"
        )
        assert "rect.top" in body, "toggleActionMenu must reference rect.top for upward flip"
