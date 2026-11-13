from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()
        self.tab_targets: list[str] = []
        self.section_ids: set[str] = set()
        self.in_section = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.add(attributes["id"])
        if tag == "button" and "data-tab" in attributes:
            self.tab_targets.append(attributes["data-tab"])
        if tag == "section" and "tab-content" in attributes.get("class", "").split():
            self.section_ids.add(attributes.get("id", ""))


def test_dashboard_navigation_targets_existing_unique_sections():
    parser = DashboardParser()
    parser.feed((ROOT / "dashboard/index.html").read_text(encoding="utf-8"))
    assert parser.tab_targets == [
        "tab-verify", "tab-metrics", "tab-benchmark", "tab-audits",
        "tab-policies", "tab-watchtower", "tab-replay",
    ]
    assert len(parser.tab_targets) == len(set(parser.tab_targets))
    assert set(parser.tab_targets) == parser.section_ids


def test_dashboard_feature_controls_exist_and_render_with_safe_dom_apis():
    parser = DashboardParser()
    parser.feed((ROOT / "dashboard/index.html").read_text(encoding="utf-8"))
    app = (ROOT / "dashboard/app.js").read_text(encoding="utf-8")
    referenced_ids = set(re.findall(r"getElementById\(['\"]([^'\"]+)", app))
    optional_status_ids = {"stat-mode", "stat-cb", "stat-db"}
    assert referenced_ids - optional_status_ids <= parser.ids
    assert ".innerHTML" not in app
    assert "textContent" in app


def test_feature_tabs_cover_expected_api_flows_and_empty_error_states():
    app = (ROOT / "dashboard/app.js").read_text(encoding="utf-8")
    html = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")
    for endpoint in (
        "/api/policies", "/api/policies/categories", "/api/watchtower/claims",
        "/api/watchtower/alerts", "/api/replay/",
    ):
        assert endpoint in app
    assert "window.openFreshGateReplay" in app
    assert "No policies yet." in app
    assert "No monitored claims." in app
    assert "No detected changes." in app
    assert "No verification history exists for this claim yet." in app
    assert "Could not load Watchtower:" in app
    assert "Could not load Replay:" in app
    assert "confirm(" in app
    assert "openai-api-key" in html and "tavily-api-key" in html
    assert "localStorage" not in app and "sessionStorage" not in app


def test_dashboard_styles_remain_responsive_and_reduced_motion_aware():
    styles = (ROOT / "dashboard/style.css").read_text(encoding="utf-8")
    assert "@media(max-width:800px)" in styles
    assert "@media(max-width:560px)" in styles
    assert "prefers-reduced-motion:reduce" in styles


def test_groq_dashboard_preset_uses_supported_model_and_actionable_recovery():
    app = (ROOT / "dashboard/app.js").read_text(encoding="utf-8")
    html = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")

    assert "model: 'openai/gpt-oss-20b'" in app
    assert "Groq (GPT-OSS 20B)" in html
    assert "Use GPT-OSS 20B" in app
    assert "model_not_found" in app
    assert "Claim extraction failed. Unable to verify response freshness." not in app
