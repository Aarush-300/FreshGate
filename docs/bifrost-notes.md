# Bifrost Gateway Architecture & Plugin System Notes

Research and analysis for FreshGate integration into Maxim AI's **Bifrost Gateway** (`vendor/bifrost`).

---

## 1. How Plugins Are Written and Loaded

### Language & Technology
- Bifrost plugins are written in **Go** as shared object files (`.so`), compiled with:
  ```bash
  go build -buildmode=plugin -o myplugin.so main.go
  ```
  *(Reference: `vendor/bifrost/docs/plugins/getting-started.mdx#L45-L48`)*
- WebAssembly (WASM) plugins were supported in legacy versions but are explicitly marked deprecated:
  > *"Writing WASM Plugins (Deprecated) - Legacy WebAssembly plugin guide... Use Go plugins for new development"*
  *(Reference: `vendor/bifrost/docs/plugins/getting-started.mdx#L120`)*

### Loading Mechanism
- Bifrost loads plugins dynamically at runtime using Go's standard library `plugin.Open(path)`:
  - Source file: `vendor/bifrost/framework/plugins/main.go` and `vendor/bifrost/framework/plugins/soplugin.go#L10-L69`.
  - The plugin struct `DynamicPlugin` dynamically resolves symbol lookups for lifecycle methods and hook callbacks:
    - `Init(config any) error`
    - `GetName() string`
    - `Cleanup() error`
    - `HTTPTransportPreAuthHook`, `HTTPTransportPreHook`, `HTTPTransportPostHook`, `HTTPTransportResponseHeadersHook`, `HTTPTransportStreamChunkHook`
    - `PreRequestHook`, `PreLLMHook`, `PostLLMHook`
    - `PreMCPHook`, `PostMCPHook`, `PreA2AHook`, `PostA2AHook`
- Dynamic plugins are registered in `config.json` under the `"plugins"` array:
  ```json
  "plugins": [
    {
      "enabled": true,
      "name": "freshgate",
      "path": "./plugins/freshgate.so"
    }
  ]
  ```
  *(Reference: `vendor/bifrost/.github/workflows/configs/withdynamicplugin/config.json#L10-L16`)*

### Critical Platform Constraints & Decision Gate
- **OS Support:** Go standard library plugins (`plugin.Open`) are **strictly supported only on Linux and macOS (Darwin)**. Go does **not** support `-buildmode=plugin` on Windows (`plugin: not implemented on windows`).
- **Binary Compatibility:** Dynamic plugins require dynamic builds of Bifrost compiled with the exact same Go toolchain and dependency versions (`vendor/bifrost/docs/plugins/getting-started.mdx#L75-L84`). Prebuilt standalone release binaries are statically linked.
- **Decision Gate Outcome:**
  - While Bifrost has native hook signatures (`PostLLMHook` and `HTTPTransportPostHook`), Go `.so` dynamic plugins cannot run natively on Windows host environments, and require identical Go compiler builds.
  - Therefore, we follow the architecture plan: **Phase 5 Fallback Proxy (`POST /proxy/v1/chat/completions`)**, which runs cross-platform anywhere Bifrost runs, while also providing the Go plugin design specification in `plugin/` as an upstream contribution.

---

## 2. Bifrost Hooks & Request Lifecycle

From `vendor/bifrost/framework/plugins/soplugin.go` and `vendor/bifrost/docs/plugins/getting-started.mdx#L95-L106`, the request lifecycle executes in the following sequence:

1. **`HTTPTransportPreAuthHook(ctx, req)`**: Intercepts raw HTTP request before authentication.
2. **`HTTPTransportPreHook(ctx, req)`**: Intercepts request after transport authentication before Bifrost core.
3. **`PreRequestHook(ctx, req)`**: Executes once per top-level request to make routing and provider decisions (`vendor/bifrost/docs/plugins/writing-go-plugin.mdx#L164-L168`).
4. **`PreLLMHook(ctx, req)`**: Runs per provider attempt. Can mutate request or short-circuit (`*schemas.LLMPluginShortCircuit`).
5. **Provider Execution**: Bifrost calls the upstream LLM provider (OpenAI, Anthropic, etc.).
6. **`PostLLMHook(ctx, resp, err)`**:
   - Signature: `PostLLMHook(ctx *schemas.BifrostContext, resp *schemas.BifrostResponse, bifrostErr *schemas.BifrostError) (*schemas.BifrostResponse, *schemas.BifrostError, error)`
   - *(Reference: `vendor/bifrost/framework/plugins/soplugin.go#L41`, `vendor/bifrost/docs/plugins/writing-go-plugin.mdx#L180-L184`)*
   - This **is a usable post-response hook** in Bifrost core for Go plugins: it receives the completed LLM response, allowing inspection, mutation, or error injection.
7. **`HTTPTransportPostHook(ctx, req, resp)`**:
   - Signature: `HTTPTransportPostHook(ctx *schemas.BifrostContext, req *schemas.HTTPRequest, resp *schemas.HTTPResponse) error`
   - *(Reference: `vendor/bifrost/framework/plugins/soplugin.go#L100-L105`)*
   - Intercepts HTTP responses after exiting Bifrost core. Mutates HTTP headers or body.
8. **`HTTPTransportResponseHeadersHook` & `HTTPTransportStreamChunkHook`**: For streaming responses.

---

## 3. How to Run Bifrost Locally & Send a Test Request

### Running Locally
Bifrost can be run in two ways:
1. **Via Node / NPX Wrapper**:
   ```bash
   node vendor/bifrost/npx/bifrost/bin.js --host=127.0.0.1 --port=8080
   # or
   npx -y @maximhq/bifrost --port 8080
   ```
   The wrapper automatically downloads and executes the platform-specific prebuilt binary (e.g. `bifrost-http.exe-0` on Windows) to `$LOCALAPPDATA/bifrost/v<version>/bin/`.
2. **Direct Binary Execution**:
   ```bash
   bifrost-http -port 8080 -host 127.0.0.1 -app-dir ./.bifrost-data
   ```

### Health Check
- `GET http://127.0.0.1:8080/health`
- Response: `{"components":{"db_pings":"ok"},"status":"ok"}`

### Sending a Test Chat Request
- Endpoint: `POST http://127.0.0.1:8080/v1/chat/completions`
- Payload:
  ```json
  {
    "model": "openai/gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Hello, Bifrost!"}
    ]
  }
  ```
- Bifrost returns 200 with the provider's completion when configured, or a structured 400 error (`failed to get config for provider openai: not found`) if the provider key is not yet added in `config.json`.

---

## 4. How Bifrost Caching Works

Bifrost implements caching in two layers:

### A. Semantic Cache Plugin
- Located in `vendor/bifrost/plugins/semanticcache/main.go`.
- Configuration (`vendor/bifrost/.github/workflows/configs/withsemanticcache/config.json#L3-L20`):
  Uses a `vector_store` backend (e.g. SQLite, Weaviate, Qdrant, Chroma, PgVector).
- Mechanism:
  1. In `PreLLMHook`: Converts incoming prompt messages into vector embeddings, queries the vector store within a similarity threshold.
  2. If cosine distance matches within the threshold, returns `schemas.LLMPluginShortCircuit` containing the cached `BifrostResponse` (skipping the provider call).
  3. In `PostLLMHook`: On cache misses, saves the prompt embedding + response into the vector store.

### B. Core General Cache
- Located in `vendor/bifrost/framework/gencache` and `vendor/bifrost/framework/lrucache`.
- In-memory key-value caching with LRU eviction used for config lookups, model parameter metadata, and temporary session state.
