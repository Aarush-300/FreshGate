# FreshGate: Judge Walkthrough & Demo Script (3-Minute Tour)

This script provides an exact 3-minute, high-impact demonstration of FreshGate for hackathon evaluation, addressing both **Problem 01 (Stale-Answer Detector & Real-Time Verification)** and **Problem 04 (Bifrost Gateway Integration)**.

---

## Prerequisites for Live Demo
- Local server running (`.\run.ps1` or `uvicorn server.app:app --port 8000`) or deployed Render instance.
- Dashboard opened at `http://localhost:8000` (or `https://<your-render-app>.onrender.com`).
- A free Tavily API key and your preferred LLM provider key (OpenAI, Groq, Gemini, etc.).

---

## 1. Registration & Profile Setup (30 Seconds)
1. **Navigate to the web dashboard**:
   - Register a new account (e.g. `judge@hackathon.dev` / `StrongPassword123!`).
   - Session cookie is issued via secure, authenticated HTTP-only cookies.
2. **Open Profile**:
   - Notice the status badge reads **"Profile incomplete"** (gold badge) because credentials have not been configured yet.
   - Select your provider (e.g. **OpenAI** or **Groq**).
   - Notice **Base URL** is read-only and pre-filled with the official API endpoint.
   - Enter your provider API key.
   - Click **"Load models"**: FreshGate dynamically inspects available models from the provider and populates the select dropdown.
   - Select a model (e.g. `gpt-4o-mini` or `llama-3.3-70b-versatile`).
   - Paste your **Tavily search API key** (free from [tavily.com](https://tavily.com)).
   - Click **"Save settings"**.
   - The badge updates to **"Profile complete"** (emerald green badge).
   - *Key takeaway for judges*: Credentials are encrypted server-side using AES-GCM (Fernet) per user. No keys are ever stored in `localStorage` or `sessionStorage`.

---

## 2. Verify View: Stale-Answer Detection (60 Seconds)
1. **Switch to the "Verify" tab**:
   - The provider line under the input displays: `Using OpenAI · gpt-4o-mini · Tavily key ✓`.
2. **One-Click Example**:
   - Click **"Try an example"** next to the input label.
   - A pre-filled real-world response containing time-sensitive statements appears:
     > *"As of today, Python 3.13 is the current stable release. Sam Altman serves as the CEO of OpenAI, and the standard pricing for GPT-4o input tokens is $2.50 per million tokens."*
3. **Execute Verification**:
   - Click **"Check response"**.
   - Watch the multi-step verification pipeline execute:
     1. **Claim Extraction**: Extracts atomic verifiable facts with categories (`leadership`, `pricing`, `software`).
     2. **Live Evidence Search**: Performs targeted queries using Tavily search.
     3. **LLM Evidence Judging**: Scores factual consistency against current primary sources.
4. **Inspect Claims & Sources**:
   - Inspect each claim in the table:
     - Statement, category, verdict chip (`confirmed`, `contradicted`, `partially_supported`, `unverifiable`), and **Freshness Score** (0–100).
   - Click a claim row to inspect the **Claim Folio**:
     - View exact evidence excerpts, publication date, source domain, and direct URL citation.

---

## 3. Watchtower: Continuous Background Monitoring (45 Seconds)
1. **Add to Watchtower**:
   - In the selected Claim Folio, click **"Monitor claim"**.
   - A confirmation toast appears: *"Claim added to Watchtower. Scheduled checks use the provider and Tavily key saved in your Profile."*
2. **Open the "Watchtower" tab**:
   - The monitored claim is listed with its target category, interval, current verdict, and next scheduled check.
   - Isolation: Monitored claims are strictly scoped to the logged-in user in MongoDB.
3. **Trigger Manual Check**:
   - Click **"Check now"** to execute an out-of-band immediate verification using the user's saved credentials.
4. **Inline Two-Step Removal**:
   - Click **"Remove"** on a claim.
   - The button transitions into `Remove this claim? [Remove] [Cancel]` with zero disruptive browser popups.
   - Click **[Remove]** to confirm deletion and alert cascade.

---

## 4. Replay & Policy Studio (30 Seconds)
1. **Replay View**:
   - Switch to **"Replay"** to inspect immutable verification snapshots and trace how evidence changes across checks.
2. **Policy Studio**:
   - View default freshness rules or configure custom policies (e.g., minimum source count, required authority thresholds, maximum evidence age in days).

---

## 5. Gateway Mode & Bifrost Integration (15 Seconds)
FreshGate seamlessly sits in front of AI gateways like Bifrost or acts as a drop-in OpenAI-compatible proxy.

Send a curl request to verify gateway proxy behavior:
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-FreshGate-Mode: warn" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Who is the CEO of Twitter?"}]
  }' -i
```

Notice the attached HTTP response headers:
- `X-FreshGate-Mode: warn`
- `X-FreshGate-Score: <score>`
- `X-FreshGate-Stale-Count: <count>`
- `X-FreshGate-Total-Claims: <count>`
- `X-FreshGate-Latency-Ms: <ms>`
- `X-FreshGate-Status: verified`
