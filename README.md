# frigate-discord-notify

Polls a Frigate instance's REST API for finished tracked-object events and
posts a Discord webhook notification for each one, including the object's
sub_label (custom classification) if one was assigned, plus a short GIF
preview clipped from the recording.

## Setup

The prebuilt image is published automatically to `ghcr.io` on every push to
`main`. To run it:

1. Paste the contents of [`docker-compose.yml`](docker-compose.yml) into
   your stack manager (Portainer, Dockge, Unraid Compose Manager, etc.) or
   save it as a file and run `docker compose up -d` directly.
2. Edit the two marked lines before starting it:
   - `FRIGATE_URL` — your Frigate instance's address, e.g. `http://192.168.0.50:5000`
   - `DISCORD_WEBHOOK_URL` — from your Discord channel's Integrations settings
3. Adjust `NOTIFY_LABELS` / `NOTIFY_CAMERAS` if you want to filter which
   detections trigger a notification. Leave `NOTIFY_LABELS` blank for
   "everything Frigate tracks".
4. Start the stack.

Since this repo is public, don't commit your edited compose file with real
values back to it — keep your filled-in copy local to wherever you deploy
it.

## Notes

- No MQTT broker required — polls Frigate's HTTP API every `POLL_INTERVAL`
  seconds (default 15) instead.
- Waits for an event's `end_time` before notifying, so the classification
  has reached consensus and the clip is fully recorded before it's used.
- Falls back gif → mp4 → snapshot if GIF conversion fails or produces a file
  over Discord's upload limit.
- State (last processed event time) persists in a docker volume so restarts
  don't replay history.

## Building it yourself

If you'd rather build locally instead of pulling the published image,
change `image: ghcr.io/moistnas/frigate-discord-notify:latest` to
`build: .` in `docker-compose.yml` (requires cloning this repo so the
`Dockerfile` is present alongside it).
