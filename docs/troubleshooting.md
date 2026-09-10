# Troubleshooting

## No live signals or option chain

NSE can rate-limit, change, or temporarily block public endpoints. The client
refreshes a Chrome-impersonating session and retries bounded requests. Check
`/api/health` for the last refresh and use the dashboard's refresh controls;
do not increase the polling interval below the documented minimum without
revalidating public-source limits.

## Empty technical indicators

EMA50 and the daily validation checks need sufficient stored NSE bhavcopy
history. Run the bounded 60-day backfill command from the README, then wait
for the daily 18:10 IST ingestion job. The app intentionally reports missing
data instead of inferring intraday values from daily bars.

## History disappeared after a Render deployment

This is expected on a Render Free web service: its filesystem is ephemeral.
SQLite history persists locally and through Docker Compose only when the data
directory is backed by a persistent volume.

## Alerts not arriving

Leave all alert settings blank to disable them. For ntfy or webhook delivery,
verify an HTTPS destination. For Telegram, set both
`NSE_OI_TELEGRAM_BOT_TOKEN` and `NSE_OI_TELEGRAM_CHAT_ID`; the application
rejects partial Telegram configuration at startup. Alert delivery results are
audited without storing credential values.

## Git push crashes on this Windows host

The reported `git-remote-https.exe` memory-read dialog is a local Git for
Windows transport crash, independent of the FastAPI service. Dismiss the
dialog and update/repair Git for Windows. The working tree and local tests are
not corrupted by that dialog.
