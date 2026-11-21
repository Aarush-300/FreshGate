# Adding freshness checks to Bifrost

The native Go plugin runs inside Bifrost's HTTP gateway. The Python service runs separately and owns extraction, web retrieval, scoring and audit storage. Configure the chat provider on Bifrost and the verification provider on FreshGate. They may use different credentials or models.

## Load the native plugin

1. Start FreshGate with server-level provider credentials and a shared `FRESHGATE_API_KEY`. The plugin calls `POST /api/plugin/verify`. Test service readiness at `/health/ready` from the same network as Bifrost.
2. Build on the gateway's target platform with cgo and a compatible dynamic-plugin host. The module pins Bifrost core v1.15.1 and Go 1.27.3. Go plugins require matching compiler, architecture, build settings and shared dependencies. On Windows run both Linux components under WSL2 or choose the proxy.
3. From the repository root, build and install the artifact into a directory readable by the gateway.

```bash
cd plugin
go test ./...
CGO_ENABLED=1 go build -buildmode=plugin -o freshgate.so .
```

4. Merge this entry into the gateway's existing `plugins` array. Set the shared key in the Bifrost process environment and restart it.

```json
{
  "name": "freshgate",
  "enabled": true,
  "path": "/opt/bifrost/plugins/freshgate.so",
  "config": {
    "freshgate_url": "http://127.0.0.1:8000/api/plugin/verify",
    "timeout_sec": 8,
    "fail_open": true,
    "async_mode": false
  }
}
```

Use a reachable service hostname if the applications run in different containers. `FRESHGATE_URL` overrides the **full endpoint** for the plugin. The default timeout is four seconds. `FRESHGATE_TIMEOUT_SEC`, `FRESHGATE_ASYNC_MODE` and `FRESHGATE_FAIL_OPEN` override their corresponding plugin settings.

## What happens to a completion

The transport pre-hook creates request-local storage. The post-LLM hook extracts the first choice's nonempty text from a completed chat response and submits it for verification. The transport post-hook adds the full `freshgate` result, preserving fields such as `choices` and `usage`. Check `X-FreshGate-Status`, `X-FreshGate-Stale-Count` and `X-FreshGate-Score` on a synchronous result. A missing average is represented by `unavailable`.

A streaming chunk or tool-only choice has no supported completed text for this hook. The `X-FreshGate-Plugin: active` marker alone confirms only that the transport hook ran. An async check returns before evidence is available. In synchronous mode, fail-open preserves the completion after a verifier transport failure. Fail-closed produces a gateway error.

## Use the proxy with a standard HTTP gateway

Set `BIFROST_UPSTREAM_URL` in FreshGate and direct the chat client to FreshGate's `/v1/chat/completions`. The proxy forwards the chat to Bifrost. Shadow mode schedules verification in the background, warn mode returns verification headers, and enforce mode can add the report or reject a stale response when blocking is enabled. Streaming uses a separate post-stream verification path. Running both integrations on the same request can duplicate checks.

For a complete chat request and launch instructions, see the [root integration example](../README.md#native-bifrost-plugin). [Plugin source](../plugin/freshgate_plugin.go) · [Proxy implementation](../server/app.py)
