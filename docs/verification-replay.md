# Verification Replay

Replay shows how FreshGate's evidence for a volatile claim changes over time. Every completed volatile claim check writes an append-only snapshot in the shared `FRESHGATE_FEATURE_DB` SQLite store. The result includes a per-claim `verification_id`; snapshots can also be retrieved by their stable SHA-256 claim fingerprint.

## Snapshot contents

Each snapshot records the claim fingerprint/text/category, verdict, freshness score, score dimensions, source URLs and snippets with publication dates and authority, verification timestamp, applied policy ID/name/revision, cache status, correction/explanation, confidence, and end-to-end verification duration. Watchtower checks use the same history service and store.

FreshGate captures the score dimensions it currently calculates: verdict quality, evidence recency, source authority, evidence agreement, verification confidence, and final score. This is a record of the current scorer's outputs, not a second scoring engine. Source-level differences are shown by URL, snippet, publication date, and authority score.

## Dashboard

Open Replay from a monitored claim or Watchtower alert, or paste a claim fingerprint in the Replay tab. The timeline includes verification snapshots and matching alert events. Select two snapshots to compare verdict, score delta, evidence URLs/snippets, source authority, recency and agreement dimensions, correction, and applied policy. New, removed, and changed evidence is called out separately.

## API

- `GET /api/history/claims/{fingerprint}` returns paginated snapshots, newest first.
- `GET /api/history/verifications/{verification_id}` returns a snapshot.
- `GET /api/replay/{fingerprint}` returns a chronological snapshot page and latest comparison.
- `GET /api/replay/{fingerprint}/compare?from={id}&to={id}` compares two snapshots of that claim. Either snapshot IDs or `verification_id` values are accepted.
- `GET /api/replay/{fingerprint}/timeline` returns paginated verification and alert events.

Replay APIs are read-only. They never edit or delete history. The shared SQLite store uses triggers to reject snapshot updates and deletions.

## Storage durability

Use a persistent volume and back up the file configured by `FRESHGATE_FEATURE_DB`. Render's free filesystem is ephemeral, so snapshots and Replay history can be lost on restart or redeploy. SQLite is the included single-host implementation; use managed durable storage and validate a database-backed multi-instance design before production scale. No snapshot-retention job currently deletes old records.
