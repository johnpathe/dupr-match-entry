"""Grab a DUPR access token from a real logged-in browser session.

DUPR's dashboard authenticates with HttpOnly cookies on api.dupr.com:
    __Host-dupr_at  -> access token (JWT)
    __Host-dupr_rt  -> refresh token

Playwright can read those cookies even though JS can't. This opens Chromium on
a persistent profile (`.pw-profile/`); you log in by hand once, then it writes
the access token to `.env` as DUPR_BEARER_TOKEN.

Run:
    .venv\\Scripts\\python.exe get_token.py
"""

import base64
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
PROFILE_DIR = HERE / ".pw-profile"
ENV_PATH = HERE / ".env"
START_URL = "https://dashboard.dupr.com/dashboard/browse"
AT_COOKIE = "__Host-dupr_at"
RT_COOKIE = "__Host-dupr_rt"


def log(*a) -> None:
    print(*a, flush=True)


def decode_jwt(tok: str) -> dict:
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def write_env(access: str, refresh: str | None) -> None:
    keep = []
    if ENV_PATH.exists():
        keep = [
            ln for ln in ENV_PATH.read_text().splitlines()
            if not ln.split("=", 1)[0].strip()
            in {"DUPR_BEARER_TOKEN", "DUPR_REFRESH_TOKEN"}
        ]
    keep.append(f"DUPR_BEARER_TOKEN={access}")
    if refresh:
        keep.append(f"DUPR_REFRESH_TOKEN={refresh}")
    ENV_PATH.write_text("\n".join(keep) + "\n")
    log(f"\nWrote token to {ENV_PATH}")


def find_cookies(ctx) -> tuple[str | None, str | None]:
    at = rt = None
    for c in ctx.cookies():
        if c["name"] == AT_COOKIE:
            at = c["value"]
        elif c["name"] == RT_COOKIE:
            rt = c["value"]
    return at, rt


def main() -> None:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            channel="chromium",
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(START_URL, wait_until="domcontentloaded")

        log("\n" + "=" * 62)
        log("Log in to DUPR in the Chromium window if it shows a login page.")
        log("Waiting for the session cookie...")
        log("=" * 62 + "\n")

        access = refresh = None
        for tick in range(600):
            access, refresh = find_cookies(ctx)
            if access:
                break
            if tick and tick % 15 == 0:
                log(f"...still waiting ({tick}s)")
            page.wait_for_timeout(1000)

        ctx.close()

    if not access:
        log("\nTimed out - never saw the __Host-dupr_at cookie.")
        sys.exit(1)

    claims = decode_jwt(access)
    if claims:
        import datetime as dt
        exp = claims.get("exp")
        who = claims.get("sub") or claims.get("email") or "?"
        when = dt.datetime.fromtimestamp(exp).isoformat() if exp else "?"
        log(f"Access token OK  (sub={who}, iss={claims.get('iss')}, expires {when})")

    write_env(access, refresh)
    log("Done. Run:  .venv\\Scripts\\python.exe example.py")


if __name__ == "__main__":
    main()
