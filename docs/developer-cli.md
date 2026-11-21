# Use FreshGate from the command line

The CLI calls an already running FreshGate HTTP service. From the repository root, install it into the active Python environment with `python -m pip install -e .`. The equivalent source-checkout entry point is `python -m cli`.

## Check one answer

```bash
freshgate status --url http://127.0.0.1:8000
freshgate verify --file answer.txt --format markdown
freshgate verify --text "The current supported release is version 3." --format json
```

Set `FRESHGATE_URL` to the **server base URL** or pass `--url` on the command. The Windows launcher usually starts on port 8127. For direct verification, export `FRESHGATE_OPENAI_API_KEY` and `FRESHGATE_TAVILY_API_KEY` in the CLI process. It also recognizes `OPENAI_API_KEY` and `TAVILY_API_KEY`. The CLI sends those credentials to the specified FreshGate server, so use an endpoint you trust. The browser Profile is a separate credential path.

## Evaluate a batch

Save a UTF-8 JSON file such as `answers.json`.

```json
[
  {"name": "Release notes", "answer": "Version 3 is the current stable release."},
  {"name": "Pricing FAQ", "answer": "The standard monthly subscription costs $20."}
]
```

```bash
freshgate batch --file answers.json --min-score 60 --fail-on-unverifiable --format json
```

A batch can also contain plain answer strings. Use `--warning-only` to report evaluation failures while returning success. `--timeout` sets the HTTP timeout and `--retries` controls retries for transient failures. A retry can repeat work if the server accepted the original request before the client timed out.

For verify and batch, exit code **0** means the configured evaluation passed or was warning-only, **1** means a configured condition failed, and **2** signals a CLI input, API or transport error. The status command probes readiness. From a source checkout, `freshgate benchmark --suite unit` runs the fixture-based benchmark. The explicit `--live` option uses external services and may incur charges. [Command definitions](../cli/main.py) · [Client behavior](../cli/client.py)
