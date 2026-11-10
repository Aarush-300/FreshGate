# FreshGate: Security Architecture

## 1. Principles of Defense-in-Depth

FreshGate handles untrusted inputs at two critical boundaries:
1. **Client-Facing Boundary:** Receives arbitrary LLM prompt/completion payloads and administrative API requests.
2. **External Retrieval Boundary:** Ingests live web search snippets and third-party web content, which may harbor malicious indirect prompt injections.

To achieve production-grade security, FreshGate implements multi-layered defensive controls:

```
[ Client Request ] 
       │
       ▼
1. Body Size Limit Middleware (1 MB cap)
       │
       ▼
2. Sliding Window Rate Limiter (IP-based, 60 req/min default)
       │
       ▼
3. Timing-Safe Auth (hmac.compare_digest for API & Admin keys)
       │
       ▼
4. Universal Proxy Execution
       │
       ▼
5. Web Retrieval (Tavily Search)
       │
       ▼
6. XML Sandboxing (<untrusted_web_evidence> injection shield)
       │
       ▼
7. Sanitized Response (Zero stack trace or internal path leakage)
       │
       ▼
8. Strict Security Headers (CSP script-src 'self', nosniff, DENY)
```

---

## 2. Authentication & Authorization

### 2.1 Timing-Safe Token Comparison
All API keys (`FRESHGATE_API_KEY`) and administrative tokens (`FRESHGATE_ADMIN_KEY`) are compared using Python's `hmac.compare_digest(token, expected_token)`. This eliminates side-channel timing attacks that could reveal credential character lengths or prefix values.

### 2.2 Role-Based Segregation
- **Public / Client Endpoints:** `/health`, `/v1/chat/completions`, `/proxy/v1/chat/completions`, `/verify`. If `FRESHGATE_API_KEY` is configured, client requests must supply `X-API-Key` or `Authorization: Bearer <key>`.
- **Administrative Endpoints:** `/api/cache/clear`, `/api/audits/recent`, `/api/circuit-breaker/reset`. Strictly guarded by `verify_admin_key`, requiring `X-Admin-Key` or Bearer administrative credentials.

---

## 3. Denial of Service (DoS) Protections

### 3.1 Request Body Size Limit (`RequestBodySizeLimitMiddleware`)
- Default maximum request payload: **1,048,576 bytes (1 MB)**, configurable via `FRESHGATE_MAX_BODY_SIZE_BYTES`.
- Prevents memory exhaustion attacks from unbounded prompt payloads.
- Rejects oversized requests immediately with `HTTP 413 Content Too Large` before JSON parsing occurs.

### 3.2 Sliding Window Rate Limiter (`SlidingWindowRateLimiter`)
- Evaluates request frequency across a sliding 60-second window per client IP.
- Default limit: 60 requests/minute.
- Upon exceeding threshold, returns `HTTP 429 Too Many Requests` with a calculated `Retry-After: <seconds>` header.

---

## 4. Content Security Policy & XSS Elimination

### 4.1 Strict Content Security Policy
FreshGate enforces the following HTTP security header across all responses:
```http
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: https:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```
- **Zero Inline Scripts:** Eliminates `'unsafe-inline'` from `script-src`. All executable JavaScript resides in `/static/app.js`.
- **Frame Ancestors:** `frame-ancestors 'none'` prevents clickjacking attacks.
- **X-Content-Type-Options:** `nosniff` prevents MIME-type sniffing.
- **X-Frame-Options:** `DENY`.

### 4.2 Safe DOM Manipulation
The operational dashboard (`dashboard/app.js`) exclusively uses safe DOM APIs (`document.createElement`, `textContent`, `setAttribute`). `innerHTML` and `document.write` are completely banned from the codebase.

---

## 5. URL Safety & Citation Validation

Before emitting search source URLs in client payloads or displaying them in the console, `is_safe_url(url)` verifies:
- Protocol is strictly `http` or `https`.
- Rejects dangerous schemes (`javascript:`, `data:`, `file:`, `blob:`).
- Validates fully qualified domain structure (rejects localhost loopbacks or internal RFC 1918 IPs unless explicitly allowed in test harnesses).

---

## 6. Error Sanitization & Information Leakage Defense

- When internal exceptions or upstream timeouts occur, FreshGate intercepts the error via `sanitize_error_detail(e)`.
- Internal filesystem paths (`C:\...`, `/app/...`), raw SQL queries, stack traces, and API keys are scrubbed before any error message is logged or transmitted.
- Gateway headers emit generic status tokens (e.g., `X-FreshGate-Reason: timeout` or `internal_error`), never raw tracebacks.
