# FreshGate Bifrost plugin

The native plugin build, platform requirements, configuration, and end-to-end test instructions are maintained in the root [README](../README.md#1-bifrost-setup-and-usage).

This Go shared-object plugin runs inside a dynamically linked Bifrost build on Linux or macOS. On Windows, use Ubuntu under WSL2 to build and run Linux Bifrost, or use FreshGate's cross-platform proxy; Windows Bifrost cannot load Go `.so` plugins.

## Plugin Authentication & Verification Route

The Go plugin authenticates against FreshGate's dedicated `/api/plugin/verify` endpoint:
- **Endpoint:** `POST /api/plugin/verify`
- **Authentication:** `X-API-Key` header verified against `FRESHGATE_API_KEY`.
- **Configuration:** Set `FRESHGATE_API_KEY` in the environment. The key should come from the `FRESHGATE_API_KEY` environment variable rather than hardcoding it in the JSON config file. You can configure the target verification endpoint via `freshgate_url` (defaults to `http://127.0.0.1:8000/api/plugin/verify`) or the `FRESHGATE_URL` environment variable.
