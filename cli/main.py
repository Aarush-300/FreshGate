"""Argparse entry point for the FreshGate developer CLI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from cli import __version__
from cli.client import CLIError, FreshGateClient
from cli.report import evaluate_result, render_batch, render_single


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if number <= 0 or number > 600:
        raise argparse.ArgumentTypeError("must be greater than 0 and at most 600")
    return number


def _score(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer between 0 and 100") from exc
    if not 0 <= number <= 100:
        raise argparse.ArgumentTypeError("must be an integer between 0 and 100")
    return number


def _add_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--url", default=os.getenv("FRESHGATE_URL", "http://127.0.0.1:8000"),
        help="FreshGate server URL (default: FRESHGATE_URL or %(default)s)",
    )
    parser.add_argument(
        "--timeout", type=_positive_float,
        default=os.getenv("FRESHGATE_CLI_TIMEOUT", "20"),
        help="HTTP request timeout in seconds (default: %(default)s)",
    )
    parser.add_argument("--retries", type=int, choices=range(0, 6), default=2,
                        help="Retries for transient transport/server errors (0-5; default: 2)")


def _add_evaluation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--policy-id", help="Saved policy ID to apply")
    parser.add_argument("--min-score", type=_score, help="Fail if any scored claim is below this freshness score")
    parser.add_argument("--fail-on-unverifiable", action="store_true",
                        help="Treat unverifiable claims as a failed condition")
    parser.add_argument("--warning-only", action="store_true",
                        help="Report failed conditions as warnings and always exit successfully")
    parser.add_argument("--format", choices=("text", "json", "markdown"), default="text",
                        help="Output format (default: text)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="freshgate", description="Verify answers through a FreshGate HTTP API")
    parser.add_argument("--version", action="version", version=f"freshgate {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    verify = commands.add_parser("verify", help="Verify one answer")
    source = verify.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="Answer text to verify")
    source.add_argument("--file", help="UTF-8 text file containing an answer")
    _add_connection_options(verify)
    _add_evaluation_options(verify)

    batch = commands.add_parser("batch", help="Verify answers from a JSON file")
    batch.add_argument("--file", required=True, help="JSON array of answer strings/objects or an object with an answers array")
    _add_connection_options(batch)
    _add_evaluation_options(batch)

    status = commands.add_parser("status", help="Check whether a FreshGate server is ready")
    _add_connection_options(status)
    status.add_argument("--format", choices=("text", "json"), default="text")

    benchmark = commands.add_parser("benchmark", help="Run FreshGate's existing benchmark suite")
    benchmark.add_argument("--suite", choices=("unit", "realworld", "adversarial", "all"), default="all")
    benchmark.add_argument("--live", action="store_true", help="Run existing benchmark with configured live provider APIs")
    return parser


def _client(args: argparse.Namespace) -> FreshGateClient:
    return FreshGateClient(
        args.url,
        timeout=args.timeout,
        retries=args.retries,
        openai_api_key=os.getenv("FRESHGATE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
        tavily_api_key=os.getenv("FRESHGATE_TAVILY_API_KEY") or os.getenv("TAVILY_API_KEY"),
    )


def _read_text_argument(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    try:
        return Path(args.file).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CLIError(f"Could not read answer file: {exc}") from exc


def _load_batch_file(path: str) -> list[dict[str, str]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise CLIError(f"Could not read batch file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CLIError(f"Batch file is not valid JSON: {exc.msg}") from exc
    if isinstance(value, dict):
        value = value.get("answers")
    if not isinstance(value, list) or not value:
        raise CLIError("Batch JSON must be a non-empty array or an object with a non-empty 'answers' array")
    answers: list[dict[str, str]] = []
    for index, item in enumerate(value, 1):
        if isinstance(item, str):
            answer, label, policy_id = item, f"Answer {index}", ""
        elif isinstance(item, dict):
            answer = item.get("answer", item.get("text"))
            label = item.get("name", item.get("id", f"Answer {index}"))
            policy_id = item.get("policy_id", "")
        else:
            raise CLIError(f"Batch item {index} must be a string or object with answer/text")
        if not isinstance(answer, str) or not answer.strip():
            raise CLIError(f"Batch item {index} has no non-empty answer/text")
        if len(answer) > 50_000:
            raise CLIError(f"Batch item {index} exceeds FreshGate's 50000-character limit")
        answers.append({"answer": answer, "label": str(label), "policy_id": str(policy_id or "")})
    return answers


def _run_verify(args: argparse.Namespace) -> int:
    answer = _read_text_argument(args)
    with _client(args) as client:
        result = client.verify(answer, policy_id=args.policy_id)
    evaluation = evaluate_result(
        result,
        min_score=args.min_score,
        fail_on_unverifiable=args.fail_on_unverifiable,
        warning_only=args.warning_only,
    )
    print(render_single({"result": result, "evaluation": evaluation}, args.format))
    return 0 if evaluation["passed"] else 1


def _run_batch(args: argparse.Namespace) -> int:
    answers = _load_batch_file(args.file)
    reports = []
    with _client(args) as client:
        for item in answers:
            policy_id = item["policy_id"] or args.policy_id
            result = client.verify(item["answer"], policy_id=policy_id)
            evaluation = evaluate_result(
                result,
                min_score=args.min_score,
                fail_on_unverifiable=args.fail_on_unverifiable,
                warning_only=args.warning_only,
            )
            reports.append({"label": item["label"], "result": result, "evaluation": evaluation})
    print(render_batch(reports, args.format))
    return 0 if all(report["evaluation"]["passed"] for report in reports) else 1


def _run_status(args: argparse.Namespace) -> int:
    with _client(args) as client:
        result = client.health()
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"FreshGate: {result.get('status', 'unknown')}")
        for key in ("database", "cache", "upstream"):
            if key in result:
                print(f"{key}: {result[key]}")
    return 0 if result.get("status") == "ready" else 1


def _run_benchmark(args: argparse.Namespace) -> int:
    script = Path(__file__).resolve().parents[1] / "bench" / "evaluate.py"
    if not script.is_file():
        raise CLIError("Benchmark runner is not installed; run this command from a FreshGate source checkout")
    command = [sys.executable, str(script), "--suite", args.suite]
    if args.live:
        command.append("--live")
    return subprocess.run(command, cwd=script.parent.parent, check=False).returncode


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            return _run_verify(args)
        if args.command == "batch":
            return _run_batch(args)
        if args.command == "status":
            return _run_status(args)
        if args.command == "benchmark":
            return _run_benchmark(args)
        parser.error("unknown command")
    except CLIError as exc:
        print(f"freshgate: error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("freshgate: interrupted", file=sys.stderr)
        return 130
    return 2
