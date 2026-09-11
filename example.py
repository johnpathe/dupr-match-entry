"""Starter script for the DUPR API client.

    1. .venv\\Scripts\\python.exe get_token.py   (once, to grab a session token)
    2. .venv\\Scripts\\python.exe example.py
"""

from dupr_session import make_client


def main() -> None:
    client = make_client()

    profile = client.user.get_profile()
    me = profile.get("result", profile)
    print("Logged in as:", me.get("fullName"))
    print("DUPR id:", me.get("id"))

    stats = me.get("stats") or {}
    print("Singles:", stats.get("singles"), " Doubles:", stats.get("doubles"))

    # a few more things you can do:
    # results = client.players.search_players(query="Jane Doe")
    # matches = client.matches.get_pending_matches()
    # history = client.players.get_player_match_history(me["id"])


if __name__ == "__main__":
    main()
