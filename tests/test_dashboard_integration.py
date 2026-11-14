from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "dashboard/src/App.tsx").read_text(encoding="utf-8")
API = (ROOT / "dashboard/src/api.ts").read_text(encoding="utf-8")
STYLES = (ROOT / "dashboard/src/styles.css").read_text(encoding="utf-8")


def test_dashboard_entry_mounts_the_react_application():
    html = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")
    entry = (ROOT / "dashboard/src/main.tsx").read_text(encoding="utf-8")
    assert '<div id="root"></div>' in html
    assert 'src="/src/main.tsx"' in html
    assert "createRoot(document.getElementById(\"root\")!)" in entry


def test_dashboard_keeps_existing_verification_and_feature_api_flows():
    for endpoint in (
        '"/verify"', '"/health"', '"/health/ready"', '"/metrics"',
        '"/api/provider/status"', '"/api/deployment/status"', '"/api/benchmark"',
        '"/api/providers"',
        '"/api/audits/recent"', '"/api/policies"', '"/api/policies/categories"',
        '"/api/watchtower/claims"', '"/api/watchtower/alerts?unacknowledged_only=true"',
    ):
        assert endpoint in APP
    assert "/api/replay/" in APP
    assert '"/api/watchtower/monitor"' in APP
    assert "/acknowledge" in APP
    assert "/compare?from=" in APP


def test_dashboard_has_explicit_empty_error_and_request_scoped_credential_states():
    assert "No changing claims found" in APP
    assert "No policies saved" in APP
    assert "No claims monitored" in APP
    assert "No detected changes" in APP
    assert "No verification history exists for this claim yet" in APP
    assert "X-LLM-API-Key" in APP and "X-Tavily-API-Key" in APP
    assert 'setLlmKey(""); setSearchKey("")' in APP
    assert 'localStorage.setItem("llmKey"' not in APP + API
    assert 'localStorage.setItem("searchKey"' not in APP + API
    assert "sessionStorage" not in APP + API
    assert 'role="switch"' in APP
    assert ">API key<" in APP and ">Base URL<" in APP and ">Model<" in APP
    assert '"/api/providers"' in APP
    assert "window.confirm" in APP


def test_dashboard_is_responsive_and_respects_reduced_motion():
    assert "@media (max-width: 900px)" in STYLES
    assert "@media (max-width: 700px)" in STYLES
    assert "@media (max-width: 420px)" in STYLES
    assert "prefers-reduced-motion: reduce" in STYLES


def test_production_asset_base_matches_fastapi_static_mount():
    vite = (ROOT / "dashboard/vite.config.ts").read_text(encoding="utf-8")
    server = (ROOT / "server/app.py").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert '"/static/"' in vite
    assert '"dashboard" / "dist"' in server
    assert "npm run build" in dockerfile
    assert "dashboard/dist/" in dockerfile
