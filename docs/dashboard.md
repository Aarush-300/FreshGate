# Using the FreshGate page

Open the address printed by the Windows launcher or the manual server address, normally port 8000. Register or sign in, then open **Profile**. Choose a provider, model and endpoint if required. Save the provider credentials and Tavily key. Model discovery runs from the server, so a local provider must be reachable from the machine running FastAPI.

## Verify an answer or document

1. Open **Verify**. Paste the original answer or select **Try an example**. For a document, choose a TXT, Markdown, JSON, CSV, PDF or DOCX file. The default upload limit is 10 MiB.
2. Select a saved policy if the review requires particular evidence. Run verification and inspect the checked count and status before reading the average score.
3. Open a claim. Match its category, verdict and explanation to the source snippets. Follow the source URL, check the date and look for a correction when evidence contradicts the text.
4. Choose **Monitor claim** to carry that claim and its available baseline into Watchtower. Use **Replay** to compare subsequent checks.

| Page | What to use it for |
| --- | --- |
| Policy Studio | Create, validate, duplicate and preview a policy before selecting it for verification. |
| Watchtower | Inspect enabled claims, last result, next check, retry errors and alerts. Configure email reports separately. |
| Replay | Load a fingerprint, compare two evidence snapshots and download the resulting records. |
| System | Inspect service readiness, provider status, cache and metrics. |
| Benchmarks | Read packaged evaluation results and dataset counts. Live provider performance varies. |
| Audit log | Supply the configured admin key to inspect recorded requests. A normal session alone cannot open recent audits. |

If the page loads but Verify fails, check the signed-in Profile first. A functioning gateway may be using different credentials. If the browser cannot reach any API route during frontend development, check the Vite proxy origin against the port printed by the backend. [Local setup](../README.md#run-the-freshgate-page-locally)
