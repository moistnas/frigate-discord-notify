import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import requests
import urllib3

FRIGATE_URL = os.environ.get("FRIGATE_URL", "http://frigate:5000").rstrip("/")
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "15"))
FRIGATE_VERIFY_SSL = os.environ.get("FRIGATE_VERIFY_SSL", "true").strip().lower() not in ("false", "0", "no")
if not FRIGATE_VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
NOTIFY_LABELS = {
    l.strip() for l in os.environ.get("NOTIFY_LABELS", "").split(",") if l.strip()
}
NOTIFY_CAMERAS = {
    c.strip() for c in os.environ.get("NOTIFY_CAMERAS", "").split(",") if c.strip()
}
SEND_AS = os.environ.get("SEND_AS", "gif")  # "gif" or "mp4"
GIF_DURATION = float(os.environ.get("GIF_DURATION_SECONDS", "6"))
GIF_FPS = int(os.environ.get("GIF_FPS", "8"))
GIF_WIDTH = int(os.environ.get("GIF_WIDTH", "480"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "/data/state.json"))
DISCORD_MAX_BYTES = int(os.environ.get("DISCORD_MAX_BYTES", str(8 * 1024 * 1024)))

STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_state() -> float:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())["last_event_time"]
        except (json.JSONDecodeError, KeyError):
            pass
    # first run: don't replay old history, start from now
    return time.time()


def save_state(last_event_time: float) -> None:
    STATE_FILE.write_text(json.dumps({"last_event_time": last_event_time}))


def fetch_new_events(after: float) -> list[dict]:
    resp = requests.get(
        f"{FRIGATE_URL}/api/events",
        params={"after": after, "limit": 50, "sort": "asc"},
        timeout=10,
        verify=FRIGATE_VERIFY_SSL,
    )
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "")
    if "application/json" not in content_type:
        snippet = resp.text[:300].replace("\n", " ")
        raise RuntimeError(
            f"Frigate returned non-JSON response (status {resp.status_code}, "
            f"content-type '{content_type}') from {resp.url} — got: {snippet!r}"
        )
    events = resp.json()
    # only fully-finished events: end_time is set once the object leaves frame,
    # which also means the recording clip is finalized and the sub_label
    # classifier has had its full consensus window
    return [e for e in events if e.get("end_time")]


def event_passes_filters(event: dict) -> bool:
    if NOTIFY_LABELS and event["label"] not in NOTIFY_LABELS:
        return False
    if NOTIFY_CAMERAS and event["camera"] not in NOTIFY_CAMERAS:
        return False
    return True


def download_clip(event_id: str, dest: Path) -> bool:
    resp = requests.get(
        f"{FRIGATE_URL}/api/events/{event_id}/clip.mp4",
        timeout=30,
        verify=FRIGATE_VERIFY_SSL,
    )
    if resp.status_code != 200:
        return False
    dest.write_bytes(resp.content)
    return True


def download_snapshot(event_id: str, dest: Path) -> bool:
    resp = requests.get(
        f"{FRIGATE_URL}/api/events/{event_id}/snapshot.jpg",
        params={"quality": 90},
        timeout=15,
        verify=FRIGATE_VERIFY_SSL,
    )
    if resp.status_code != 200:
        return False
    dest.write_bytes(resp.content)
    return True


def clip_to_gif(clip_path: Path, gif_path: Path) -> bool:
    palette_path = gif_path.with_suffix(".palette.png")
    vf_scale = f"fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=lanczos"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-t", str(GIF_DURATION), "-i", str(clip_path),
                "-vf", f"{vf_scale},palettegen", str(palette_path),
            ],
            check=True, capture_output=True,
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-t", str(GIF_DURATION), "-i", str(clip_path),
                "-i", str(palette_path),
                "-lavfi", f"{vf_scale}[x];[x][1:v]paletteuse",
                str(gif_path),
            ],
            check=True, capture_output=True,
        )
        return gif_path.exists()
    except subprocess.CalledProcessError as e:
        print(f"ffmpeg failed: {e.stderr.decode(errors='replace')[-500:]}")
        return False
    finally:
        palette_path.unlink(missing_ok=True)


def build_embed(event: dict) -> dict:
    label = event["label"].replace("_", " ").title()
    sub_label = event.get("sub_label")
    title = f"{label} — {sub_label}" if sub_label else label
    zones = ", ".join(event.get("zones", [])) or "—"
    score = event.get("top_score") or event.get("score")

    fields = [
        {"name": "Camera", "value": event["camera"], "inline": True},
        {"name": "Zone", "value": zones, "inline": True},
    ]
    if score:
        fields.append({"name": "Confidence", "value": f"{score * 100:.0f}%", "inline": True})

    return {
        "title": title,
        "color": 0x5865F2,
        "fields": fields,
        "timestamp": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(event["start_time"])
        ),
    }


def notify_discord(event: dict, media_path: Path | None) -> None:
    embed = build_embed(event)
    payload = {"embeds": [embed]}

    if media_path and media_path.exists():
        payload["embeds"][0]["image"] = {"url": f"attachment://{media_path.name}"}
        with open(media_path, "rb") as f:
            resp = requests.post(
                DISCORD_WEBHOOK_URL,
                data={"payload_json": json.dumps(payload)},
                files={"file": (media_path.name, f, "image/gif" if media_path.suffix == ".gif" else "video/mp4")},
                timeout=30,
            )
    else:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)

    if resp.status_code >= 300:
        print(f"Discord webhook failed ({resp.status_code}): {resp.text[:500]}")


def process_event(event: dict) -> None:
    event_id = event["id"]
    print(f"Processing event {event_id}: {event['label']} on {event['camera']} (sub_label={event.get('sub_label')})")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        media_path = None

        if event.get("has_clip"):
            clip_path = tmp / f"{event_id}.mp4"
            if download_clip(event_id, clip_path):
                if SEND_AS == "gif":
                    gif_path = tmp / f"{event_id}.gif"
                    if clip_to_gif(clip_path, gif_path) and gif_path.stat().st_size <= DISCORD_MAX_BYTES:
                        media_path = gif_path
                    elif clip_path.stat().st_size <= DISCORD_MAX_BYTES:
                        print("GIF conversion failed or too large, falling back to mp4")
                        media_path = clip_path
                elif clip_path.stat().st_size <= DISCORD_MAX_BYTES:
                    media_path = clip_path

        if media_path is None and event.get("has_snapshot"):
            snap_path = tmp / f"{event_id}.jpg"
            if download_snapshot(event_id, snap_path):
                media_path = snap_path

        notify_discord(event, media_path)


def main() -> None:
    last_event_time = load_state()
    print(f"Starting, resuming from t={last_event_time}")

    while True:
        try:
            events = fetch_new_events(last_event_time)
            for event in events:
                if event_passes_filters(event):
                    try:
                        process_event(event)
                    except Exception as e:
                        print(f"Failed to process event {event['id']}: {e}")
                last_event_time = max(last_event_time, event["end_time"])
                save_state(last_event_time)
        except Exception as e:
            print(f"Poll loop error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
