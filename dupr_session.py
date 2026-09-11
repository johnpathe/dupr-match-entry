"""Build a ready-to-use DUPRClient for this laptop.

Two local quirks this handles:

1. This machine sits behind a TLS-inspecting proxy, so Python's bundled CA list
   rejects DUPR's certs. `truststore` makes Python use the Windows cert store
   instead, which trusts the corporate root CA.

2. DUPR's current API (api.dupr.com) authenticates with the `__Host-dupr_at`
   cookie, not a Bearer header. `get_token.py` pulls that cookie out of a real
   browser session and stores it in `.env` as DUPR_BEARER_TOKEN.

Usage:
    from dupr_session import make_client
    client = make_client()
    print(client.user.get_profile())
"""

import os
from pathlib import Path

import truststore

truststore.inject_into_ssl()  # must happen before requests opens any connection

from dupr_api import DUPRClient  # noqa: E402

BASE_URL = "https://api.dupr.com"
API_HOST = "api.dupr.com"
AT_COOKIE = "__Host-dupr_at"
ENV_PATH = Path(__file__).with_name(".env")


def _load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def make_client() -> DUPRClient:
    _load_env()
    token = os.environ.get("DUPR_BEARER_TOKEN")
    if not token:
        raise SystemExit(
            "No token found. Run:  .venv\\Scripts\\python.exe get_token.py"
        )

    client = DUPRClient(base_url=BASE_URL, version="v1.0")
    client.session.cookies.set(AT_COOKIE, token, domain=API_HOST, secure=True)

    refresh = os.environ.get("DUPR_REFRESH_TOKEN")
    if refresh:
        client.session.cookies.set(
            "__Host-dupr_rt", refresh, domain=API_HOST, secure=True
        )
    return client
