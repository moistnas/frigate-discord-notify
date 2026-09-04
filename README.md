# frigate-discord-notify

Polls a Frigate instance's REST API for finished tracked-object events and
posts a Discord webhook notification for each one, including the object's
sub_label (custom classification) if one was assigned, plus a short GIF
preview clipped from the recording.

## Setup

1. Copy `.env.example` to `.env` and fill in:
   - `DISCORD_WEBHOOK_URL` — from your Discord channel's Integrations settings
   - `FRIGATE_URL` — see the comment in `.env.example` for same-network vs.
     separate-network guidance
2. Adjust `NOTIFY_LABELS` / `NOTIFY_CAMERAS` in `.env` if you want to filter
   which detections trigger a notification. Leave blank for "everything
   Frigate tracks".
3. `docker compose up -d --build`
4. Watch it work: `docker compose logs -f`

## Notes

- No MQTT broker required — polls Frigate's HTTP API every `POLL_INTERVAL`
  seconds (default 15) instead.
- Waits for an event's `end_time` before notifying, so the classification
  has reached consensus and the clip is fully recorded before it's used.
- Falls back gif → mp4 → snapshot if GIF conversion fails or produces a file
  over Discord's upload limit.
- State (last processed event time) persists in `./data/state.json` so
  restarts don't replay history.
