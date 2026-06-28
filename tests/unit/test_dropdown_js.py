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
    """Extract the toggleActionMenu function body.

    Uses ``function `` as boundary — robust against varying whitespace between functions.
    """
    m = re.search(
        r"function toggleActionMenu\(.*?\)\s*\{(.*?)\n\}\s*(?:\n+function\s|\Z)",
        html,
        re.DOTALL,
    )
    return m.group(1) if m else ""


class TestDropdownJS:
    def test_stop_propagation_present(self):
        html = _read_index_html()
        body = _extract_toggle_body(html)
        assert "stopPropagation" in body, (
            "toggleActionMenu must call event.stopPropagation() to prevent "
            "the click-outside listener from closing the menu immediately"
        )

    def test_event_parameter_explicit(self):
        html = _read_index_html()
        # The onclick attribute must pass `event` as first argument
        assert "toggleActionMenu(event," in html, (
            "onclick must pass the event object explicitly: toggleActionMenu(event, ...)"
        )

    def test_menu_opened_at_guard_present(self):
        html = _read_index_html()
        assert "_menuOpenedAt" in html, (
            "Script must define _menuOpenedAt guard variable for scroll debounce"
        )

    def test_scroll_listener_has_debounce_guard(self):
        html = _read_index_html()
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

    def test_uses_offset_height_measurement(self):
        html = _read_index_html()
        body = _extract_toggle_body(html)
        assert "offsetHeight" in body, (
            "toggleActionMenu must measure real clone height via offsetHeight "
            "instead of hard-coding menuHeight"
        )

    def test_uses_template_element(self):
        html = _read_index_html()
        # The source menu must be wrapped in a <template> to avoid wasted DOM nodes
        assert '<template id="' in html, (
            "renderRowActions must wrap the .action-menu source in a <template> element"
        )
