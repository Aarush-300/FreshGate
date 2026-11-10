# FreshGate Bifrost Native Plugin (Path A)

This directory contains the native Go plugin implementation for Maxim AI's **Bifrost Gateway**.

## Architecture & Lifecycle Hooks

The plugin registers with Bifrost's dynamic plugin loader (`vendor/bifrost/framework/plugins/soplugin.go`) and taps into two hooks:

1. **`PostLLMHook`**: Runs immediately after the upstream provider generates the assistant response.
   - Extracts the assistant text.
   - Calls the FreshGate verification engine (local `/verify` endpoint).
   - Enriches `resp.ExtraFields["freshgate"]` with extracted volatile claims, verdicts, and source citations.
   - Respects `fail_open` and `timeout_sec`.
2. **`HTTPTransportPostHook`**: Injects `X-FreshGate-*` HTTP response headers.

## Building and Compiling (Linux / macOS)

Dynamic Go plugins require compilation with the target platform's Go toolchain matching Bifrost:

```bash
# Compile shared object
go build -buildmode=plugin -o freshgate.so freshgate_plugin.go
```

## Configuration in Bifrost `config.json`

Add FreshGate to the `plugins` section of your Bifrost configuration:

```json
{
  "plugins": [
    {
      "enabled": true,
      "name": "freshgate",
      "path": "./plugins/freshgate.so",
      "config": {
        "freshgate_url": "http://127.0.0.1:8000/verify",
        "timeout_sec": 4,
        "fail_open": true,
        "async_mode": false
      }
    }
  ]
}
```

*Note: For Windows environments or precompiled static Bifrost releases where Go `.so` dynamic loading is unsupported, use the **FreshGate Fallback Proxy (Path B)** in `server/app.py`, which is 100% drop-in compatible with zero gateway binary changes.*
