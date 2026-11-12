"""HTTP client for the public FreshGate API."""

from __future__ import annotations

import time
from typing import Any, Optional
from urllib.parse import urlparse

import httpx


class CLIError(Exception):
    """Expected configuration, transport, or API error for CLI display."""


class FreshGateClient:
    RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        timeout: float = 20.0,
        retries: int = 2,
        openai_api_key: Optional[str] = None,
        tavily_api_key: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
        sleep=time.sleep,
    ):
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise CLIError("FreshGate URL must be an HTTP(S) origin without embedded credentials")
        if parsed.query or parsed.fragment:
            raise CLIError("FreshGate URL must not include a query string or fragment")
        if timeout <= 0 or timeout > 600:
            raise CLIError("Request timeout must be greater than 0 and at most 600 seconds")
        if retries < 0 or retries > 5:
            raise CLIError("Retries must be between 0 and 5")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self._sleep = sleep
        self._secrets = [key for key in (openai_api_key, tavily_api_key) if key]
        self._headers = {}
        if openai_api_key:
            self._headers["X-OpenAI-API-Key"] = openai_api_key
        if tavily_api_key:
            self._headers["X-Tavily-API-Key"] = tavily_api_key
        self._http = httpx.Client(timeout=timeout, transport=transport, follow_redirects=False)

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _redact(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def _redact_payload(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact(value)
        if isinstance(value, list):
            return [self._redact_payload(item) for item in value]
        if isinstance(value, dict):
            return {key: self._redact_payload(item) for key, item in value.items()}
        return value

    def _request(self, method: str, path: str, *, body: Optional[dict[str, Any]] = None) -> Any:
        last_error = "FreshGate request failed"
        for attempt in range(self.retries + 1):
            try:
                response = self._http.request(
                    method,
                    f"{self.base_url}{path}",
                    json=body,
                    headers=self._headers,
                    timeout=self.timeout,
                )
            except httpx.TimeoutException as exc:
                last_error = f"FreshGate request timed out after {self.timeout:g} seconds"
                if attempt < self.retries:
                    self._sleep(min(0.25 * (2**attempt), 2.0))
                    continue
                raise CLIError(last_error) from exc
            except httpx.RequestError as exc:
                last_error = f"Could not connect to FreshGate: {exc.__class__.__name__}"
                if attempt < self.retries:
                    self._sleep(min(0.25 * (2**attempt), 2.0))
                    continue
                raise CLIError(last_error) from exc

            if response.status_code in self.RETRYABLE_STATUS_CODES and attempt < self.retries:
                self._sleep(min(0.25 * (2**attempt), 2.0))
                continue
            if not response.is_success:
                detail = f"HTTP {response.status_code}"
                try:
                    payload = response.json()
                    detail = str(payload.get("detail") or detail) if isinstance(payload, dict) else detail
                except ValueError:
                    pass
                raise CLIError(self._redact(f"FreshGate API error: {detail}"))
            try:
                return self._redact_payload(response.json())
            except ValueError as exc:
                raise CLIError("FreshGate returned an invalid JSON response") from exc
        raise CLIError(last_error)

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health/ready")

    def verify(self, answer: str, *, policy_id: Optional[str] = None) -> dict[str, Any]:
        if not isinstance(answer, str) or not answer.strip():
            raise CLIError("Answer text must not be empty")
        if len(answer) > 50_000:
            raise CLIError("Answer text exceeds FreshGate's 50000-character limit")
        body: dict[str, Any] = {"answer": answer}
        if policy_id:
            body["policy_id"] = policy_id
        return self._request("POST", "/verify", body=body)
