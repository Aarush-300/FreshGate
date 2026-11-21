# Monitor claims after publication

Watchtower persists a claim and schedules another check against the evidence. Enrollment from Verify can include the current verdict, score and source list as a baseline. The first scheduled check becomes due after the monitoring interval. Use **Check now** for an immediate attempt.

## Enroll and inspect

1. Save working AI-provider and Tavily settings in the owner's Profile.
2. Choose **Monitor claim** on a volatile claim or add the text and category in Watchtower. Set the interval and, if appropriate, an enabled policy that covers the category.
3. Inspect the monitored record's last check, last successful check, next check and error. The monitoring API accepts a standard interval from one hour through 30 days.
4. Open the claim's history after a check. An alert can identify selected verdict changes, a score threshold crossing, material evidence changes or repeated insufficient evidence. Acknowledge an alert once reviewed.

The scheduler polls for due claims and obtains leases before starting work. Its default concurrency is two. A failed attempt can be retried with backoff. After the configured retry threshold it returns to the regular interval. A failed recheck can retain the previous successful result. Read its timestamp alongside the latest attempt status. The backend must be running for scheduled work to advance.

## Email delivery is a separate schedule

Configure a sender and one supported transport on the server. Selection order is Brevo, Resend, SendGrid, then SMTP. Save a recipient and digest interval in Watchtower. The test-email action exercises transport configuration. A scheduled digest summarizes saved claims and active alerts, including pending claims. Email frequency controls delivery independently of the claim recheck interval.

The email schedule supports a seconds interval, custom units and a limited set of cron-style presets mapped to intervals. Delivery occurs on a poll after the schedule becomes due. Check `next_run_at`, `last_sent_at` and the configured recipient when investigating delays. Pausing mail and pausing a monitored claim are separate actions.

MongoDB scopes monitors to their owners. Local SQLite supports the single-account workflow. Check errors about missing credentials in the monitoring owner's Profile. [Scheduler](../core/watchtower.py) · [Watchtower routes](../server/routes/watchtower.py)
