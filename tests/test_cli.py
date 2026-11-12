from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from cli.client import CLIError, FreshGateClient
from cli.main import main
from cli.report import evaluate_result, render_single


def sample_result():
    return {
        "claims": [
            {"id": "c1", "text": "Version 2.4 is current", "type": "version", "verdict": "confirmed", "score": 88,
             "sources": [{"url": "https://docs.example.com/version", "domain": "docs.example.com"}]},
            {"id": "c2", "text": "A claim without evidence", "type": "version", "verdict": "unverifiable", "score": None, "sources": []},
        ],
        "summary": {"total": 2, "checked": 2, "stale": 0, "avg_score": 88},
        "policy_evaluation": None,
    }


class FakeClient:
    def __init__(self, *_args, **_kwargs):
        self.answers = []

    def __enter__(self): return self
    def __exit__(self, *_args): pass
    def verify(self, answer, *, policy_id=None):
        self.answers.append((answer, policy_id))
        return sample_result()
    def health(self): return {"status": "ready", "database": "connected", "cache": "connected"}


def test_http_client_posts_json_with_request_scoped_provider_headers():
    seen = {}

    def handle(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["headers"] = request.headers
        return httpx.Response(200, json={"claims": []})

    with FreshGateClient(
        "http://freshgate.test/", openai_api_key="openai-private-key", tavily_api_key="tavily-private-key",
        transport=httpx.MockTransport(handle),
    ) as client:
        assert client.verify("A sample answer", policy_id="policy-123") == {"claims": []}
    assert seen["method"] == "POST"
    assert seen["url"] == "http://freshgate.test/verify"
    assert seen["body"] == {"answer": "A sample answer", "policy_id": "policy-123"}
    assert seen["headers"]["X-OpenAI-API-Key"] == "openai-private-key"
    assert seen["headers"]["X-Tavily-API-Key"] == "tavily-private-key"


def test_http_client_redacts_credentials_from_report_payloads():
    client = FreshGateClient(
        "http://fg.test", openai_api_key="openai-secret",
        transport=httpx.MockTransport(lambda _request: httpx.Response(
            200, json={"claims": [{"explanation": "provider repeated openai-secret"}]}
        )),
    )
    try:
        result = client.verify("text")
    finally:
        client.close()
    assert result["claims"][0]["explanation"] == "provider repeated [REDACTED]"


def test_http_client_retries_transient_status_and_redacts_secrets():
    calls = []
    waits = []

    def handle(_request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, json={"detail": "temporary"})
        return httpx.Response(200, json={"status": "ready"})

    client = FreshGateClient("http://fg.test", retries=2, sleep=waits.append, transport=httpx.MockTransport(handle))
    try:
        assert client.health()["status"] == "ready"
    finally:
        client.close()
    assert len(calls) == 2 and waits == [0.25]

    failing = FreshGateClient(
        "http://fg.test", retries=0, openai_api_key="secret-value",
        transport=httpx.MockTransport(lambda _request: httpx.Response(400, json={"detail": "secret-value is invalid"})),
    )
    try:
        with pytest.raises(CLIError) as error:
            failing.verify("text")
    finally:
        failing.close()
    assert "secret-value" not in str(error.value)
    assert "[REDACTED]" in str(error.value)


@pytest.mark.parametrize("url,timeout,retries", [
    ("file:///tmp", 20, 0),
    ("https://user:pass@fg.test", 20, 0),
    ("http://fg.test", 0, 0),
    ("http://fg.test", 20, 6),
])
def test_http_client_rejects_invalid_configuration(url, timeout, retries):
    with pytest.raises(CLIError):
        FreshGateClient(url, timeout=timeout, retries=retries)


def test_api_timeout_and_server_unavailable_are_clear_and_retried():
    sleeps = []

    def timeout_handler(request):
        raise httpx.ReadTimeout("provider details", request=request)

    client = FreshGateClient("http://fg.test", retries=1, sleep=sleeps.append,
                             transport=httpx.MockTransport(timeout_handler))
    try:
        with pytest.raises(CLIError, match="timed out"):
            client.health()
    finally:
        client.close()
    assert sleeps == [0.25]

    def offline_handler(request):
        raise httpx.ConnectError("offline", request=request)

    offline = FreshGateClient("http://fg.test", retries=0, transport=httpx.MockTransport(offline_handler))
    try:
        with pytest.raises(CLIError, match="Could not connect to FreshGate"):
            offline.health()
    finally:
        offline.close()


def test_cli_text_and_file_commands_apply_thresholds_and_dont_fail_unverifiable_by_default(monkeypatch, capsys):
    fake = FakeClient()
    monkeypatch.setattr("cli.main._client", lambda _args: fake)
    code = main(["verify", "--text", "Version 2.4 is current", "--min-score", "80", "--format", "json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["evaluation"]["unverifiable_count"] == 1
    assert report["evaluation"]["passed"] is True
    assert fake.answers[-1] == ("Version 2.4 is current", None)

    path = Path(__file__).resolve().parents[1] / f".freshgate-cli-{uuid.uuid4().hex}.txt"
    try:
        path.write_text("Answer text from a file", encoding="utf-8")
        code = main(["verify", "--file", str(path), "--policy-id", "policy-xyz", "--format", "markdown"])
        output = capsys.readouterr().out
    finally:
        path.unlink(missing_ok=True)
    assert code == 0
    assert "# FreshGate verification" in output
    assert fake.answers[-1] == ("Answer text from a file", "policy-xyz")


def test_cli_unverifiable_flag_threshold_and_warning_only(monkeypatch, capsys):
    monkeypatch.setattr("cli.main._client", lambda _args: FakeClient())
    assert main(["verify", "--text", "claim", "--fail-on-unverifiable"]) == 1
    assert "unverifiable" in capsys.readouterr().out

    assert main(["verify", "--text", "claim", "--min-score", "90"]) == 1
    assert "minimum freshness score" in capsys.readouterr().out

    assert main(["verify", "--text", "claim", "--min-score", "90", "--warning-only", "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["evaluation"]["conditions_met"] is False
    assert report["evaluation"]["warning_only"] is True


def test_score_threshold_fails_when_verifiable_claim_has_no_score():
    result = {"claims": [{"verdict": "confirmed", "score": None}]}
    evaluation = evaluate_result(result, min_score=70)
    assert evaluation["passed"] is False
    assert evaluation["unscored_count"] == 1
    assert "freshness score missing" in evaluation["failures"][0]


def test_batch_json_markdown_and_policy_rejection(monkeypatch, capsys):
    class PolicyFake(FakeClient):
        def verify(self, answer, *, policy_id=None):
            self.answers.append((answer, policy_id))
            result = sample_result()
            result["policy_evaluation"] = {"decision": "rejected", "accepted": False}
            return result

    fake = PolicyFake()
    monkeypatch.setattr("cli.main._client", lambda _args: fake)
    path = Path(__file__).resolve().parents[1] / f".freshgate-batch-{uuid.uuid4().hex}.json"
    try:
        path.write_text(json.dumps({"answers": [
            {"id": "doc-a", "text": "First answer"},
            "Second answer",
        ]}), encoding="utf-8")
        code = main(["batch", "--file", str(path), "--policy-id", "policy-default", "--format", "json"])
        report = json.loads(capsys.readouterr().out)
        assert code == 1 and report["total"] == 2
        assert fake.answers == [("First answer", "policy-default"), ("Second answer", "policy-default")]
        assert report["reports"][0]["evaluation"]["policy_rejected"] is True

        code = main(["batch", "--file", str(path), "--warning-only", "--format", "markdown"])
        markdown = capsys.readouterr().out
    finally:
        path.unlink(missing_ok=True)
    assert code == 0
    assert "# FreshGate batch report" in markdown


def test_cli_reports_invalid_batch_and_missing_file_without_traceback(capsys):
    code = main(["batch", "--file", "no-such-freshgate-batch.json"])
    captured = capsys.readouterr()
    assert code == 2
    assert "Could not read batch file" in captured.err
    assert "Traceback" not in captured.err


def test_cli_reports_invalid_utf8_file_without_traceback(capsys):
    path = Path(__file__).resolve().parents[1] / f".freshgate-invalid-{uuid.uuid4().hex}.txt"
    try:
        path.write_bytes(bytes([0xFF]))
        code = main(["verify", "--file", str(path)])
        captured = capsys.readouterr()
    finally:
        path.unlink(missing_ok=True)
    assert code == 2
    assert "Could not read answer file" in captured.err
    assert "Traceback" not in captured.err


def test_status_and_benchmark_commands_delegate(monkeypatch, capsys):
    monkeypatch.setattr("cli.main._client", lambda _args: FakeClient())
    assert main(["status"]) == 0
    assert "FreshGate: ready" in capsys.readouterr().out

    calls = []
    monkeypatch.setattr("cli.main.subprocess.run", lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0))
    assert main(["benchmark", "--suite", "unit"]) == 0
    assert calls[0][0][-2:] == ["--suite", "unit"]
