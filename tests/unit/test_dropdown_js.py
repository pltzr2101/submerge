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


class TestTvSearch:
    """Validate parseTvPath, normalizeSearch, and renderTable TV grouping in index.html."""

    def test_parse_tv_path_present(self):
        html = _read_index_html()
        assert "function parseTvPath(" in html, (
            "index.html must define parseTvPath() for TV path parsing"
        )
        assert ".indexOf('/tv/')" in html, "parseTvPath must search for /tv/ segment"

    def test_normalize_search_present(self):
        html = _read_index_html()
        assert "function normalizeSearch(" in html, (
            "index.html must define normalizeSearch() for fuzzy search matching"
        )
        # Must strip dots, spaces, hyphens, underscores
        assert "/[\\s.\\-_]/g" in html or "/[\\s.\\-_]/" in html, (
            "normalizeSearch must strip dots, spaces, hyphens, underscores"
        )

    def test_search_state_present(self):
        html = _read_index_html()
        assert "let currentSearch = ''" in html or "let currentSearch=''" in html, (
            "Script must define currentSearch state variable"
        )

    def test_collapsed_sets_present(self):
        html = _read_index_html()
        assert "collapsedSeries" in html, "Script must define collapsedSeries Set"
        assert "collapsedSeasons" in html, "Script must define collapsedSeasons Set"

    def test_group_header_css_present(self):
        css_path = Path(__file__).parent.parent.parent / "src" / "submerge" / "static" / "style.css"
        css = css_path.read_text(encoding="utf-8")
        assert ".group-header-series" in css, "CSS must define .group-header-series"
        assert ".group-header-season" in css, "CSS must define .group-header-season"
        assert ".group-chevron" in css, "CSS must define .group-chevron"

    def test_chk_video_class_preserved(self):
        html = _read_index_html()
        # _renderFlatRows must emit class="chk-video" with data-video-path
        assert 'class="chk-video"' in html, (
            "Episode rows must carry class=chk-video for batch operations"
        )
        assert "data-video-path" in html, (
            "Episode rows must carry data-video-path for batch operations"
        )

    def test_search_input_html_present(self):
        html = _read_index_html()
        assert 'id="searchInput"' in html, "Toolbar must contain a search input with id=searchInput"
        assert 'class="search-input"' in html, "Search input must have class=search-input"
