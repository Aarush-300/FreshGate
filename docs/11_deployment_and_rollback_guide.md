# Deploy, validate and roll back FreshGate

A single-user installation can use SQLite. The Compose stack includes FreshGate, Bifrost, MongoDB and Redis. Compose connects FreshGate to Bifrost through the HTTP proxy. A native plugin deployment requires a compatible gateway build.

## Start a deployment

1. For a local installation, follow the [root setup](../README.md#run-the-freshgate-page-locally). The Windows launcher prints the selected port, starting at 8127. The manual example uses 8000.
2. For Compose, prepare the environment from the example if needed, preserving existing settings. Configure a persistent session secret, separate operator and integration keys, and server verification credentials. Check which variables Compose passes into each container.
3. Run the following from the repository root.

```bash
docker compose up --build -d
docker compose ps
curl -fsS http://127.0.0.1:8000/health/ready
```

4. Configure a chat provider on Bifrost. Verify a dashboard answer using a Profile, then send a chat request through FreshGate. Check the completion, verification status and audit source. For a native deployment, perform the [plugin loading check](bifrost-notes.md) on that host.
5. Configure HTTPS, secure cookies, durable storage and trusted proxy settings before opening a shared deployment. The supplied Render manifest uses MongoDB and temporary paths for some local files. Evaluate their lifetime and whether the runtime stays awake for monitoring. [Compose configuration](../docker-compose.yml) · [Render manifest](../render.yaml)

## Roll back an application release

Record the running image identifier, configuration version and plugin/toolchain pairing. Before rollout, back up accounts, feature storage, audit files and the matching session secret. Verify the backup using an isolated restore.

If the new deployment degrades verification, capture an affected request's status and correlation ID. Route traffic back to the previous application image and compatible configuration using your deployment platform. Restore a compatible gateway and native plugin together. Recheck readiness, login, verification and an existing Replay record. Preserve persistent volumes during application rollback. Restoring older database contents can discard newer writes and requires a separate recovery decision.
