"""Exit-condition evaluation and safe text/JSON/Markdown output."""

from __future__ import annotations

import json
from typing import Any, Optional


def evaluate_result(
    result: dict[str, Any],
    *,
    min_score: Optional[int] = None,
    fail_on_unverifiable: bool = False,
    warning_only: bool = False,
) -> dict[str, Any]:
    claims = result.get("claims") or []
    low_score_claims = [
        claim for claim in claims
        if min_score is not None and isinstance(claim.get("score"), int) and claim["score"] < min_score
    ]
    unverifiable_claims = [
        claim for claim in claims if claim.get("verdict") in {None, "unverifiable"}
    ]
    unscored_claims = [
        claim for claim in claims
        if claim.get("verdict") not in {None, "unverifiable"} and claim.get("score") is None
    ]
    policy = result.get("policy_evaluation") or {}
    policy_rejected = policy.get("decision") == "rejected"
    failures = []
    if low_score_claims:
        failures.append("minimum freshness score not met")
    if min_score is not None and unscored_claims:
        failures.append("freshness score missing for a verifiable claim")
    if policy_rejected:
        failures.append("selected policy rejected the verification")
    if fail_on_unverifiable and unverifiable_claims:
        failures.append("verification contains unverifiable claims")
    return {
        "passed": not failures or warning_only,
        "conditions_met": not failures,
        "warning_only": warning_only,
        "failures": failures,
        "min_score": min_score,
        "fail_on_unverifiable": fail_on_unverifiable,
        "unverifiable_count": len(unverifiable_claims),
        "low_score_count": len(low_score_claims),
        "unscored_count": len(unscored_claims),
        "policy_rejected": policy_rejected,
    }


def _safe_markdown(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def render_single(report: dict[str, Any], output_format: str) -> str:
    result = report["result"]
    evaluation = report["evaluation"]
    status = "WARN (non-blocking)" if evaluation.get("warning_only") and evaluation["failures"] else ("PASS" if evaluation["passed"] else "FAIL")
    if output_format == "json":
        return json.dumps(report, indent=2, ensure_ascii=False)
    claims = result.get("claims") or []
    if output_format == "markdown":
        lines = ["# FreshGate verification", "", f"**Status:** {status}", ""]
        lines += ["| Claim | Verdict | Score | Evidence |", "|---|---|---:|---|"]
        for claim in claims:
            sources = claim.get("sources") or []
            links = ", ".join(f"[{_safe_markdown(source.get('domain') or source.get('url'))}]({source.get('url', '')})" for source in sources)
            lines.append("| {} | {} | {} | {} |".format(
                _safe_markdown(claim.get("text")), _safe_markdown(claim.get("verdict")),
                _safe_markdown(claim.get("score")), links or "—",
            ))
        if evaluation["failures"]:
            lines += ["", "**Conditions not met:** " + "; ".join(evaluation["failures"])]
        return "\n".join(lines)

    lines = [f"FreshGate: {status}"]
    if not claims:
        lines.append("No volatile claims were checked.")
    for claim in claims:
        lines.append(f"- {claim.get('verdict') or 'unknown'} · score {claim.get('score') if claim.get('score') is not None else '—'} · {claim.get('text', '')}")
        for source in claim.get("sources") or []:
            lines.append(f"    {source.get('url', '')}")
    if evaluation["failures"]:
        lines.append("Conditions not met: " + "; ".join(evaluation["failures"]))
    return "\n".join(lines)


def render_batch(reports: list[dict[str, Any]], output_format: str) -> str:
    batch = {
        "passed": all(report["evaluation"]["passed"] for report in reports),
        "total": len(reports),
        "passed_count": sum(1 for report in reports if report["evaluation"]["passed"]),
        "failed_count": sum(1 for report in reports if not report["evaluation"]["passed"]),
        "warning_count": sum(1 for report in reports if report["evaluation"].get("warning_only") and report["evaluation"]["failures"]),
        "reports": reports,
    }
    if output_format == "json":
        return json.dumps(batch, indent=2, ensure_ascii=False)
    if output_format == "markdown":
        batch_status = "WARN (non-blocking)" if batch["warning_count"] else ("PASS" if batch["passed"] else "FAIL")
        lines = ["# FreshGate batch report", "", f"**Result:** {batch_status} ({batch['passed_count']}/{batch['total']} passed, {batch['warning_count']} warning-only)", ""]
        lines += ["| # | Answer | Result | Claims | Conditions |", "|---:|---|---|---:|---|"]
        for index, report in enumerate(reports, 1):
            lines.append("| {} | {} | {} | {} |".format(
                index,
                _safe_markdown(report.get("label")),
                "WARN" if report["evaluation"].get("warning_only") and report["evaluation"]["failures"] else ("PASS" if report["evaluation"]["passed"] else "FAIL"),
                len(report["result"].get("claims") or []),
                _safe_markdown("; ".join(report["evaluation"]["failures"]) or "—"),
            ))
        return "\n".join(lines)
    batch_status = "WARN (non-blocking)" if batch["warning_count"] else ("PASS" if batch["passed"] else "FAIL")
    lines = [f"FreshGate batch: {batch_status} ({batch['passed_count']}/{batch['total']} passed, {batch['warning_count']} warning-only)"]
    for index, report in enumerate(reports, 1):
        evaluation = report["evaluation"]
        item_status = "WARN" if evaluation.get("warning_only") and evaluation["failures"] else ("PASS" if evaluation["passed"] else "FAIL")
        detail = f" · {'; '.join(evaluation['failures'])}" if evaluation["failures"] else ""
        lines.append(f"{index}. {item_status} · {report.get('label')}{detail}")
    return "\n".join(lines)
