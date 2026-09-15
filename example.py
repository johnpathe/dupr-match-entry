"""Starter script: talks to DUPR with plain requests calls, no client library.

    1. .venv\\Scripts\\python.exe get_token.py   (once, to grab a session token)
    2. .venv\\Scripts\\python.exe example.py
"""

from dupr_session import BASE_URL, make_client


def main() -> None:
    client = make_client()

    r = client.session.get(f"{BASE_URL}/user/v1.0/profile", timeout=15)
    r.raise_for_status()
    me = r.json()["result"]
    print("Logged in as:", me.get("fullName"))
    print("DUPR id:", me.get("id"))

    stats = me.get("stats") or {}
    print("Singles:", stats.get("singles"), " Doubles:", stats.get("doubles"))

    # a few more things you can do (see README for the endpoints match_app.py
    # and stats.py actually use):
    # r = client.session.post(f"{BASE_URL}/player/v1.0/search",
    #                          json={"query": "Jane Doe", "limit": 10, "offset": 0,
    #                                "includeUnclaimedPlayers": True, "filter": {}})
    # r = client.session.get(f"{BASE_URL}/player/v1.0/{player_id}")


if __name__ == "__main__":
    main()
