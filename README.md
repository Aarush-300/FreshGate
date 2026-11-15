# FreshGate

FreshGate is an intelligent freshness and factual verification gateway for AI applications. It intercepts, extracts, and verifies time-sensitive factual claims in LLM responses against current primary web evidence in real-time, providing actionable freshness scores and continuous scheduled monitoring.

Includes a fast FastAPI backend (Python 3.11), a React/Vite/TypeScript dashboard with the high-contrast **Graphite Moss** dark theme, multi-tenant MongoDB persistence, and native/proxy integrations with the Bifrost AI Gateway.

---

## 🏆 Hackathon Problem Mapping

FreshGate is engineered to directly solve two primary hackathon problem tracks:

### Problem 01: Stale-Answer Detector & Real-Time Verification
- **Automated Claim Extraction:** Uses targeted OpenAI-compatible LLM extraction or high-speed heuristics to isolate atomic, verifiable factual statements and categorize them (e.g., `leadership`, `pricing`, `software`, `policy`, `statistics`).
- **Live Search & Evidence Retrieval:** Queries live web sources using Tavily Search to gather up-to-date primary citations and timestamped snippets.
- **Freshness Scoring & Verdicts:** An LLM judge scores factual alignment and freshness (0–100) and assigns unambiguous verdicts: `confirmed`, `contradicted`, `partially_supported`, or `unverifiable`.
- **Watchtower (Continuous Background Monitoring):** Users can monitor critical claims (e.g. CEO changes, price changes, API deprecations) on scheduled intervals. When evidence shifts, Watchtower generates actionable alerts and logs audit history.
- **Judge-Ready One-Click Demo:** The Verify page includes a **"Try an example"** button that pre-populates a time-sensitive prompt (CEO identity, pricing, and latest software release) for immediate evaluation.

### Problem 04: Bifrost AI Gateway Extension
- **OpenAI-Compatible Drop-In Proxy:** Route chat completion requests through FreshGate (`/v1/chat/completions` or `/proxy/v1/chat/completions`) before or after Bifrost.
- **Comprehensive Verification Headers:** Transparently emits `X-FreshGate-Score`, `X-FreshGate-Stale-Count`, `X-FreshGate-Mode`, `X-FreshGate-Status`, and `X-FreshGate-Latency-Ms`.
- **Enforcement & Guardrail Modes:**
  - `shadow`: Runs verification asynchronously in the background; returns LLM response immediately.
  - `warn`: Annotates response with factual warnings and freshness headers without blocking.
  - `enforce`: Blocks responses containing contradicted or stale claims with HTTP 422 policy violations.
- **Fail-Open Resilience & Circuit Breakers:** Built-in circuit breakers prevent gateway bottlenecks; if upstream timeouts or search outages occur, FreshGate fails open cleanly without dropping client traffic.
- **Native Go Plugin:** Includes a dynamic CGO plugin (`plugin/freshgate_plugin.go`) for native Bifrost builds on Linux/macOS.

---

## 🎨 Screenshots & UI Design

The FreshGate dashboard features a custom **Graphite Moss** dark mode palette designed for high contrast, legibility, and zero brown/muddy tones:
- **Base Background:** Deep Graphite (`#0E1311`) and Surface (`#151B18`)
- **Accent:** Emerald Moss (`#6CCB95`) and Forest Green (`#3E9B6B`)
- **Semantic Signals:** Rust (`#F2907F`), Amber Gold (`#E2B75A`), and Slate Info (`#8AA4C4`)

```
+-------------------------------------------------------------------------+
| FreshGate | Verify   Watchtower   Policy Studio   Replay   System   Profile |
+-------------------------------------------------------------------------+
|  VERIFY A RESPONSE                                                      |
|  [ Try an example ]                                                     |
|  "Python 3.13 is current stable. Sam Altman is CEO of OpenAI..."        |
|  [ Check response ]                                                     |
|                                                                         |
|  RESULTS                                                                |
|  - Sam Altman is CEO of OpenAI     [ CONFIRMED ]   Score 95   [Sources] |
|  - Python 3.13 is current stable   [ CONFIRMED ]   Score 98   [Sources] |
|  - GPT-4o input is $2.50 / 1M      [ CONFIRMED ]   Score 92   [Sources] |
+-------------------------------------------------------------------------+
```

---

## 🚀 Quick Start (Local)

### Prerequisites
- Python 3.11+
- Node.js 22+ & npm
- A MongoDB instance (local `mongodb://localhost:27017` or MongoDB Atlas free cluster)

### Option A: Local Run (PowerShell / Bash)

1. **Clone and setup virtual environment**:
   ```bash
   git clone https://github.com/<USERNAME>/freshgate.git
   cd freshgate
   python -m venv .venv
   ```

2. **Activate environment & install dependencies**:
   - Windows PowerShell:
     ```powershell
     .\.venv\Scripts\Activate.ps1
     pip install -r requirements.txt
     ```
   - Linux/macOS:
     ```bash
     source .venv/bin/activate
     pip install -r requirements.txt
     ```

3. **Configure Environment**:
   ```bash
   cp .env.example .env
   ```
   Set `MONGODB_URI` and `FRESHGATE_SESSION_SECRET` in `.env`.

4. **Build Dashboard**:
   ```bash
   cd dashboard
   npm ci
   npm run build
   cd ..
   ```

5. **Start FreshGate**:
   ```bash
   uvicorn server.app:app --reload --host 127.0.0.1 --port 8000
   ```
   Open `http://localhost:8000` in your browser.

---

### Option B: Docker Compose (Full Stack)

Starts FreshGate, MongoDB 7, Redis, and Bifrost Gateway:
```bash
docker compose up --build
```
FreshGate will be accessible at `http://localhost:8000` and Bifrost at `http://localhost:8080`.

---

## ☁️ Deploy on Render (Step-by-Step)

FreshGate is configured for deployment on Render's **Free Plan** with **zero persistent disk costs** using MongoDB Atlas free tier for durable storage.

### Step 1: Create Free MongoDB Atlas Cluster
1. Sign up at [mongodb.com/atlas](https://www.mongodb.com/atlas).
2. Create a free **M0 Sandbox** cluster.
3. In **Database Access**, create a user (e.g. `freshgate-admin`) and password.
4. In **Network Access**, click **Add IP Address** and choose **Allow Access From Anywhere** (`0.0.0.0/0`) because Render's free instances use dynamic egress IPs.
5. In **Database** → **Connect** → choose **Drivers** (Python 3.11+) and copy the connection string:
   `mongodb+srv://<username>:<password>@cluster0.abcde.mongodb.net/?retryWrites=true&w=majority`

### Step 2: Push to GitHub
Ensure all your changes are committed to your GitHub repository:
```bash
git push origin main
```

### Step 3: Deploy via Render Blueprint
1. Open your [Render Dashboard](https://dashboard.render.com/).
2. Click **New +** → **Blueprint**.
3. Select your GitHub repository. Render will automatically read [`render.yaml`](render.yaml).
4. When prompted for environment variables:
   - `MONGODB_URI`: Paste your MongoDB Atlas connection string from Step 1.
5. Click **Apply**. Render builds the Docker image and provisions the web service.

### Step 4: Configure User Profile
1. Open your deployed URL (`https://<your-app>.onrender.com`).
2. Register a new user account.
3. Navigate to **Profile**:
   - Select your provider (e.g., **OpenAI**, **Groq**, **Google Gemini**, or **Together**).
   - Enter your provider API key.
   - Click **"Load models"** to dynamically fetch supported models.
   - Enter your free **Tavily search API key** (from [tavily.com](https://tavily.com)).
   - Click **"Save settings"**.

> [!NOTE]
> **Free Web Service Sleep Behavior:**
> Render free web services spin down after 15 minutes of inactivity, which pauses background Watchtower scheduling. For persistent continuous monitoring, either:
> 1. Set up a free external ping service (e.g. [UptimeRobot](https://uptimerobot.com) or [cron-job.org](https://cron-job.org)) hitting `https://<your-app>.onrender.com/health/live` every 10 minutes, OR
> 2. Upgrade the Render service to the **Starter Plan** ($7/month) in `render.yaml`.

---

## 🔒 Security & Isolation Architecture

- **Per-User MongoDB Isolation:** Every policy, monitored claim, alert, and verification snapshot is strictly filtered by the authenticated user's `owner_id`. User B can never read, modify, or delete User A's claims or history.
- **Server-Side Encrypted Credentials:** User API keys and search tokens are encrypted at rest using AES-GCM (Fernet) keys derived from `FRESHGATE_SESSION_SECRET`. Credentials are **never stored in browser storage** (`localStorage` / `sessionStorage`) and are never sent back in plain text to the browser.
  > [!WARNING]
  > Changing `FRESHGATE_SESSION_SECRET` in production will invalidate previously encrypted user credentials; users will simply need to re-enter their API keys in Profile.
- **SSRF Guard (`validate_base_url`):** In production mode (`FRESHGATE_ENV=production`), custom provider URLs pointing to loopback (`127.0.0.1`, `localhost`), private RFC-1918 subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local IPs, or cloud instance metadata services (`169.254.169.254`) are strictly blocked.
- **Offline Provider Notice:** Local offline providers (e.g., Ollama, LM Studio) running on `localhost` cannot be contacted by cloud deployments (such as Render) unless exposed via an HTTPS tunnel (e.g., Cloudflare Tunnel, ngrok).
- **Shared Evidence Cache:** The web evidence cache is shared across tenants to maximize hit rates and minimize redundant search latency on public facts. It indexes verified public factual claims by hash, category, and jurisdiction, and contains zero user credentials or private context.

### Where audit records come from

All verifications across the platform write immutable, append-only records to the audit store with one of the following `source` tags:
- `verify`: Direct verifications submitted via the dashboard Verify view or `POST /verify`.
- `proxy`: Synchronous proxy chat completions via `/v1/chat/completions` or `/proxy/v1/chat/completions`.
- `proxy_stream`: SSE streaming completions where verification completes asynchronously once all tokens are received.
- `proxy_async`: Shadow-mode completions executed as background tasks (`FRESHGATE_MODE=shadow`).
- `watchtower`: Scheduled continuous background checks performed by the Watchtower service.
- `plugin`: Dedicated verifications received from the native Bifrost Go plugin via `POST /api/plugin/verify`.

---

## 🌐 Using FreshGate in Front of Bifrost (Gateway Mode)

FreshGate can sit in front of Bifrost as an OpenAI-compatible gateway proxy.

### Proxy Chat Completion
Send a chat request directly to FreshGate's proxy endpoint:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-FreshGate-Mode: warn" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Who is the current CEO of Microsoft?"}
    ]
  }' -i
```

### Attached Verification Headers

| Header | Description | Example |
|---|---|---|
| `X-FreshGate-Mode` | Active verification mode | `shadow`, `warn`, `enforce`, `async` |
| `X-FreshGate-Score` | Mean freshness score (0–100) | `96.5` |
| `X-FreshGate-Stale-Count` | Number of stale/contradicted claims | `0` |
| `X-FreshGate-Total-Claims` | Total claims extracted | `2` |
| `X-FreshGate-Checked-Claims`| Claims checked against live search | `2` |
| `X-FreshGate-Latency-Ms` | Latency incurred by verification | `412` |
| `X-FreshGate-Status` | Verification status or fail-open notice | `verified`, `fail-open` |
| `X-FreshGate-Circuit-Breaker`| Circuit breaker state | `closed`, `open` |

---

## 🧪 Testing & Verification

Run the Python automated test suite:
```bash
python -m pytest -q
```
All 197 test cases pass, verifying:
- Mongo multi-tenant isolation and ownership scoping
- Profile settings round-trip and Fernet secret encryption
- Append-only audit log recording across `/verify`, proxy modes (`shadow`, `warn`, `enforce`), streaming SSE, and Watchtower
- Zero SQLite database pollution in repo root during test runs
- Chunked transfer body size limits and pure ASGI middleware handling
- SSRF network guard in production mode
- Password hashing offloaded from the event loop and user enumeration protections
- Watchtower claim leasing, alerting, and alert cascading
- Bifrost proxy endpoints, fail-open circuits, and distributed sliding-window rate limiters

Build and verify the React frontend:
```bash
cd dashboard
npm run build
```
Produces a clean production bundle with zero TypeScript warnings or errors, compliant with strict CSP (`style-src 'self'`).

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
