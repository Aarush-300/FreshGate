"""FreshGate Knowledge Base Export API Endpoints.

Provides executive summaries, temporal trends, prediction accuracy, and
ISO 25964-compliant knowledge base exports.
"""

import asyncio
import logging
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlsplit
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from core.history import (
    export_executive_summary,
    export_temporal_trends,
    export_prediction_accuracy,
    export_knowledge_base,
)
from core.persistence import get_feature_store
from server.users import current_user

logger = logging.getLogger("freshgate.export")

router = APIRouter(prefix="/api/export", tags=["export"])


@router.get("/claim/{fingerprint}/executive-summary")
async def get_executive_summary(
    fingerprint: str,
    req: Request,
    limit: int = Query(default=100, ge=1, le=1000),
):
    """Get executive summary of claim verification history."""
    store = get_feature_store().for_owner(current_user(req)["_id"])
    result = await asyncio.to_thread(export_executive_summary, fingerprint, limit=limit, store=store)
    return result


@router.get("/claim/{fingerprint}/temporal-trends")
async def get_temporal_trends(
    fingerprint: str,
    req: Request,
    limit: int = Query(default=100, ge=1, le=1000),
):
    """Get temporal trends with decay visualization data."""
    store = get_feature_store().for_owner(current_user(req)["_id"])
    result = await asyncio.to_thread(export_temporal_trends, fingerprint, limit=limit, store=store)
    return result


@router.get("/claim/{fingerprint}/prediction-accuracy")
async def get_prediction_accuracy(
    fingerprint: str,
    req: Request,
):
    """Get prediction accuracy comparison for a claim."""
    store = get_feature_store().for_owner(current_user(req)["_id"])
    result = await asyncio.to_thread(export_prediction_accuracy, fingerprint, store=store)
    return result


@router.get("/claim/{fingerprint}/knowledge-base")
async def get_knowledge_base(
    fingerprint: str,
    req: Request,
    format: Literal["json", "xml", "csv"] = Query(default="json"),
    download: bool = False,
):
    """Export ISO 25964-compliant knowledge base entry."""
    store = get_feature_store().for_owner(current_user(req)["_id"])
    result = await asyncio.to_thread(export_knowledge_base, fingerprint, format=format, store=store)
    if download and format in ("xml", "csv"):
        if format not in result:
            raise HTTPException(404, "No verification history found.")
        mime = "application/xml" if format == "xml" else "text/csv"
        return Response(result[format], media_type=mime, headers={"Content-Disposition": "attachment"})
    return result


@router.get("/claim/{fingerprint}/full-report")
async def get_full_report(
    fingerprint: str,
    req: Request,
    format: Literal["json", "html"] = Query(default="json"),
    download: bool = False,
):
    """Get comprehensive report combining all export types."""
    store = get_feature_store().for_owner(current_user(req)["_id"])

    # Gather all export data
    executive = await asyncio.to_thread(export_executive_summary, fingerprint, limit=100, store=store)
    temporal = await asyncio.to_thread(export_temporal_trends, fingerprint, limit=100, store=store)
    predictions = await asyncio.to_thread(export_prediction_accuracy, fingerprint, store=store)
    kb = await asyncio.to_thread(export_knowledge_base, fingerprint, format="json", store=store)

    full_report = {
        "claim_fingerprint": fingerprint,
        "executive_summary": executive,
        "temporal_trends": temporal,
        "prediction_accuracy": predictions,
        "knowledge_base_entry": kb,
        "generated_at": __import__("time").time(),
    }

    if format == "html":
        # Generate HTML report
        html = _generate_html_report(full_report)
        if download:
            return Response(html, media_type="text/html", headers={"Content-Disposition": 'attachment; filename="freshgate-report.html"'})
        return {"html": html, "filename": f"freshgate-report-{fingerprint[:8]}.html"}

    return full_report


def _generate_html_report(report: dict) -> str:
    """Generate HTML report from combined export data."""
    executive = report.get("executive_summary", {})
    temporal = report.get("temporal_trends", {})
    kb = report.get("knowledge_base_entry", {})

    html_parts = [
        """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>FreshGate Verification Report</title>
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 900px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
                .container { background: white; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }
                .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 40px; }
                .header h1 { margin: 0; font-size: 32px; font-weight: 700; }
                .header p { margin: 12px 0 0; opacity: 0.9; font-size: 16px; }
                .content { padding: 40px; }
                .section { margin-bottom: 40px; }
                .section h2 { font-size: 22px; font-weight: 600; color: #333; border-bottom: 2px solid #667eea; padding-bottom: 10px; margin-bottom: 20px; }
                .section h3 { font-size: 16px; font-weight: 600; color: #444; margin: 20px 0 10px; }
                .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 20px; }
                .stat-card { background: #f8f9fa; border: 1px solid #e1e5e9; border-radius: 8px; padding: 20px; }
                .stat-card .label { font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
                .stat-card .value { font-size: 28px; font-weight: 700; color: #333; }
                .stat-card.confirmed .value { color: #28a745; }
                .stat-card.contradicted .value { color: #dc3545; }
                .stat-card.warning .value { color: #ffc107; }
                .verdict-badge { display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; text-transform: capitalize; }
                .verdict-badge.confirmed { background: #d4edda; color: #155724; }
                .verdict-badge.contradicted { background: #f8d7da; color: #721c24; }
                .verdict-badge.partially_supported { background: #fff3cd; color: #856404; }
                .verdict-badge.unverifiable { background: #e2e3e5; color: #383d41; }
                .timeline { border-left: 2px solid #667eea; padding-left: 20px; margin: 20px 0; }
                .timeline-item { position: relative; padding-bottom: 20px; }
                .timeline-item:before { content: ''; position: absolute; left: -26px; top: 4px; width: 12px; height: 12px; border-radius: 50%; background: #667eea; border: 3px solid white; box-shadow: 0 0 0 2px #667eea; }
                .timeline-item:last-child:before { background: #28a745; box-shadow: 0 0 0 2px #28a745; }
                .timeline-date { font-size: 13px; color: #666; margin-bottom: 4px; }
                .timeline-score { font-size: 18px; font-weight: 700; color: #333; }
                .timeline-verdict { margin-top: 4px; }
                .decay-table { width: 100%; border-collapse: collapse; font-size: 14px; }
                .decay-table th, .decay-table td { padding: 12px; text-align: left; border-bottom: 1px solid #e1e5e9; }
                .decay-table th { background: #f8f9fa; font-weight: 600; color: #555; }
                .source-list { margin-top: 10px; }
                .source-item { padding: 8px 0; border-bottom: 1px solid #f0f0f0; font-size: 14px; }
                .source-item:last-child { border-bottom: none; }
                .source-item a { color: #667eea; text-decoration: none; }
                .source-item a:hover { text-decoration: underline; }
                .footer { text-align: center; padding: 30px; color: #999; font-size: 12px; background: #f8f9fa; border-top: 1px solid #e1e5e9; }
            </style>
        </head>
        <body>
            <div class="container">
        """,
        f"""
                <div class="header">
                    <h1>📊 FreshGate Verification Report</h1>
                    <p>Claim: {escape(str(kb.get('claim_text') or 'Unknown')[:100])}...</p>
                    <p>Category: {escape(str(kb.get('category', 'Unknown')))} • Generated: {datetime.fromtimestamp(report.get('generated_at', 0), timezone.utc).strftime('%B %d, %Y %H:%M UTC')}</p>
                </div>
                <div class="content">
        """,
    ]

    # Executive Summary Section
    html_parts.append("""
                <div class="section">
                    <h2>📋 Executive Summary</h2>
    """)

    verdict_dist = executive.get("verdict_distribution", {})
    html_parts.append(f"""
                    <div class="stats-grid">
                        <div class="stat-card confirmed">
                            <div class="label">Total Verifications</div>
                            <div class="value">{executive.get('total_verifications', 0)}</div>
                        </div>
                        <div class="stat-card">
                            <div class="label">Average Score</div>
                            <div class="value">{executive.get('average_score', 'N/A')}</div>
                        </div>
                        <div class="stat-card confirmed">
                            <div class="label">Score Trend</div>
                            <div class="value" style="font-size: 18px;">{executive.get('score_trend', 'stable').title()}</div>
                        </div>
                    </div>
    """)

    if verdict_dist:
        html_parts.append("""
                    <h3>Verdict Distribution</h3>
                    <div style="display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px;">
        """)
        for verdict, count in verdict_dist.items():
            html_parts.append(f'<span class="verdict-badge {escape(str(verdict))}">{escape(str(verdict.replace("_", " ").title()))}: {escape(str(count))}</span>')
        html_parts.append("</div>")

    html_parts.append("</div>")

    # Knowledge Base Entry
    html_parts.append(f"""
                <div class="section">
                    <h2>📄 Knowledge Base Entry</h2>
                    <p><strong>Claim:</strong> {escape(str(kb.get('claim_text', 'N/A')))}</p>
                    <p><strong>Category:</strong> {escape(str(kb.get('category', 'N/A')))}</p>
                    <p><strong>Current Verdict:</strong> <span class="verdict-badge {escape(str(kb.get('current_verdict', 'unknown')))}">{escape(str(kb.get('current_verdict', 'unknown').replace('_', ' ').title()))}</span></p>
                    <p><strong>Current Score:</strong> {escape(str(kb.get('current_score', 'N/A')))}/100</p>
                    <p><strong>Last Verified:</strong> {datetime.fromtimestamp(kb.get('last_verified', 0), timezone.utc).strftime('%B %d, %Y %H:%M UTC') if kb.get('last_verified') else 'N/A'}</p>
                    <p><strong>Confidence:</strong> {escape(str(kb.get('confidence', 'N/A')))}</p>
    """)

    if kb.get("explanation"):
        html_parts.append(f'<p><strong>Explanation:</strong> {escape(str(kb.get("explanation")))}</p>')

    # Sources
    sources = kb.get("sources", [])
    if sources:
        html_parts.append("""
                    <h3>Evidence Sources</h3>
                    <div class="source-list">
        """)
        for src in sources:
            url = str(src.get("url") or "")
            domain = escape(str(src.get("domain") or "Unknown"))
            try:
                parsed = urlsplit(url)
                linkable = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            except ValueError:
                linkable = False
            label = f'<a href="{escape(url)}" target="_blank" rel="noreferrer">{domain}</a>' if linkable else domain
            snippet = escape(str(src.get("snippet") or "")[:150])
            html_parts.append(f'<div class="source-item">{label} — {snippet}</div>')
        html_parts.append("</div>")

    html_parts.append("</div>")

    # Temporal Trends
    decay_data = temporal.get("decay_data", [])
    if decay_data:
        html_parts.append("""
                <div class="section">
                    <h2>📈 Temporal Trends</h2>
        """)

        half_life = temporal.get("half_life_estimate_seconds")
        if half_life:
            html_parts.append(f'<p><strong>Estimated Half-Life:</strong> {half_life // 3600}h {((half_life % 3600) // 60)}m</p>')

        html_parts.append("""
                    <h3>Score Timeline</h3>
                    <div class="timeline">
        """)

        for item in decay_data[:20]:  # Limit to 20 items
            checked_at = item.get("checked_at")
            score = item.get("freshness_score")
            verdict = item.get("verdict")
            if checked_at:
                date_str = datetime.fromtimestamp(checked_at, timezone.utc).strftime('%b %d, %Y %H:%M')
                html_parts.append(f"""
                        <div class="timeline-item">
                            <div class="timeline-date">{date_str}</div>
                            <div class="timeline-score">Score: {escape(str(score))}/100</div>
                            <div class="timeline-verdict"><span class="verdict-badge {escape(str(verdict))}">{escape(str(verdict.replace('_', ' ').title()))}</span></div>
                        </div>
                """)

        html_parts.append("</div></div>")

    # Prediction Accuracy
    html_parts.append("""
                <div class="section">
                    <h2>🔮 Prediction Accuracy</h2>
    """)

    predictions = report.get("prediction_accuracy", {})
    if predictions.get("note"):
        html_parts.append(f'<p style="color: #666;">{escape(str(predictions["note"]))}</p>')
    else:
        html_parts.append('<p>Prediction accuracy tracking requires historical predicted vs actual data.</p>')

    html_parts.append("</div>")

    html_parts.append("""
                </div>
                <div class="footer">
                    <p>Generated by FreshGate AI Gateway — Automated Fact Verification</p>
                    <p>This report is based on immutable verification snapshots stored in the FreshGate feature store.</p>
                </div>
            </div>
        </body>
        </html>
    """)

    return "\n".join(html_parts)