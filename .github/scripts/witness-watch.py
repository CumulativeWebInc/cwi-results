#!/usr/bin/env python3
"""CWI reboot-witness -> GitHub notifier check.

Polls the Cloudflare reboot-witness Worker's /status endpoint and classifies
the result against the persisted state file. Emits a JSON verdict for the
workflow; the workflow opens/closes a GitHub issue on NEW_OUTAGE / RECOVERED.

$0. No new accounts. Never touches heartbeats, only reads /status.
DRY_RUN=true uses a MOCKED outage response and never writes the state file,
so a test can prove the notification path without claiming a real outage.
"""
import json
import os
import sys
import urllib.request

WITNESS_URL = os.environ.get("WITNESS_URL", "").rstrip("/")
STATE_PATH = os.environ.get("STATE_PATH", ".github/witness-watch-state.json")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


def fetch_status():
    if DRY_RUN:
        # Mocked: simulates a NEW outage for the notification-path test.
        # Never claims a real outage; never touches the real state file.
        return {
            "total_outages_recorded": 999,
            "in_outage": True,
            "last_beat": "2026-10-01T00:00:00.000Z",
            "recent_outages": [
                {
                    "start": "2026-10-01T00:00:00Z",
                    "end": "2026-10-01T00:04:12Z",
                    "duration_s": 252,
                }
            ],
        }
    req = urllib.request.Request(
        WITNESS_URL + "/status", headers={"User-Agent": "cwi-witness-watch/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except Exception as e:  # network failure is a watcher problem, not a VM outage
        print(json.dumps({"event": "FETCH_FAIL", "detail": str(e)[:200]}))
        sys.exit(0)


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"total": 0, "in_outage": False}


def main():
    status = fetch_status()
    total = int(status["total_outages_recorded"])
    in_out = bool(status["in_outage"])
    st = load_state()
    last_total = int(st.get("total", 0))
    last_in = bool(st.get("in_outage", False))

    if not DRY_RUN:
        with open(STATE_PATH, "w") as f:
            json.dump({"total": total, "in_outage": in_out}, f, indent=2)

    event, detail = "OK", f"total_outages={total}"
    if total > last_total:
        recent = (status.get("recent_outages") or [{}])[-1]
        detail = (
            f"{recent.get('start')} -> {recent.get('end')} "
            f"= {recent.get('duration_s')}s"
        )
        event = "NEW_OUTAGE"
    elif in_out and not last_in:
        event, detail = "STILL_OUT", "outage in progress (no heartbeat 7.5+ min)"
    elif not in_out and last_in:
        event, detail = "RECOVERED", "heartbeat resumed"

    out = {
        "event": event,
        "detail": detail,
        "total": total,
        "in_outage": in_out,
        "dry_run": DRY_RUN,
    }
    print(json.dumps(out))
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            for k, v in out.items():
                f.write(f"{k}={v}\n")


main()
