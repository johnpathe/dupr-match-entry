"""Local web app for entering weekly DUPR match results.

Run:
    .venv\\Scripts\\python.exe match_app.py
Then open http://127.0.0.1:5057 in a browser.

Everything is local -- roster.json holds your season's regular players, and
nothing reaches DUPR until you click "Submit to DUPR" in the browser and
confirm. The /api/preview endpoint (used by the "Preview" button) builds the
exact payload and never calls DUPR at all.

roster.json is gitignored on purpose (see .gitignore) -- it has real player
names and DUPR IDs and is meant to stay on your machine only. It's bootstrapped
from roster.example.json the first time you run this if it doesn't exist yet.
"""
import json
import shutil
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from dupr_session import make_client

HERE = Path(__file__).parent
ROSTER_PATH = HERE / "roster.json"
ROSTER_EXAMPLE_PATH = HERE / "roster.example.json"

if not ROSTER_PATH.exists() and ROSTER_EXAMPLE_PATH.exists():
    shutil.copy(ROSTER_EXAMPLE_PATH, ROSTER_PATH)

app = Flask(__name__, static_folder=None)
_client = None


def client():
    global _client
    if _client is None:
        _client = make_client()
    return _client


def load_roster():
    return json.loads(ROSTER_PATH.read_text())


def save_roster(data):
    ROSTER_PATH.write_text(json.dumps(data, indent=2))


def build_match_payload(event, location, date, club_id, team1_ids, team2_ids, games):
    """games: list of [team1_score, team2_score], 1-5 entries."""
    def team_obj(ids, side):
        t = {f"game{i+1}": (games[i][side] if i < len(games) else -1) for i in range(5)}
        t["player1"] = ids[0]
        t["player2"] = ids[1] if len(ids) > 1 else None
        return t

    team1 = team_obj(team1_ids, 0)
    team2 = team_obj(team2_ids, 1)
    t1_wins = sum(1 for a, b in games if a > b)
    t2_wins = sum(1 for a, b in games if b > a)
    team1["winner"] = t1_wins > t2_wins
    team2["winner"] = t2_wins > t1_wins
    fmt = "DOUBLES" if len(team1_ids) > 1 or len(team2_ids) > 1 else "SINGLES"

    payload = {
        "event": event,
        "eventDate": date,
        "location": location,
        "matchType": "SIDE_ONLY",
        "format": fmt,
        "team1": team1,
        "team2": team2,
    }
    if club_id:
        payload["clubId"] = club_id
    return payload


@app.route("/")
def index():
    return send_from_directory(HERE, "match_app.html")


@app.route("/api/roster", methods=["GET"])
def get_roster():
    return jsonify(load_roster())


@app.route("/api/roster", methods=["POST"])
def set_roster():
    data = request.get_json(force=True)
    save_roster(data)
    return jsonify({"ok": True})


@app.route("/api/search_players")
def search_players():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    try:
        r = client().session.post(
            "https://api.dupr.com/player/v1.0/search",
            json={"query": q, "limit": 10, "offset": 0,
                  "includeUnclaimedPlayers": True, "filter": {}},
            timeout=20,
        )
        r.raise_for_status()
        hits = r.json().get("result", {}).get("hits", [])
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    out = [{
        "id": h["id"],
        "name": h.get("fullName"),
        "duprId": h.get("duprId"),
        "doubles": (h.get("ratings") or {}).get("doubles"),
        "shortAddress": h.get("shortAddress"),
    } for h in hits]
    return jsonify(out)


def _build_from_request(body):
    roster = load_roster()
    event = body.get("event") or roster["eventName"]
    location = body.get("location") or roster["location"]
    date = body["date"]
    club_id = roster.get("clubId")
    payloads = []
    for m in body["matches"]:
        games = [[int(g[0]), int(g[1])] for g in m["games"] if g[0] != "" and g[1] != ""]
        payloads.append(build_match_payload(
            event, location, date, club_id,
            [int(x) for x in m["team1"]], [int(x) for x in m["team2"]], games,
        ))
    return payloads


@app.route("/api/preview", methods=["POST"])
def preview():
    body = request.get_json(force=True)
    try:
        payloads = _build_from_request(body)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"payloads": payloads})


@app.route("/api/submit", methods=["POST"])
def submit():
    body = request.get_json(force=True)
    try:
        payloads = _build_from_request(body)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    results = []
    for p in payloads:
        try:
            r = client().session.put(
                "https://api.dupr.com/match/v1.0/save", json=p, timeout=30
            )
            ok = r.status_code < 300
            try:
                data = r.json()
            except Exception:
                data = {"raw": r.text[:300]}
            results.append({"ok": ok, "status": r.status_code, "response": data, "sent": p})
        except Exception as e:
            results.append({"ok": False, "error": str(e), "sent": p})
    return jsonify({"results": results})


if __name__ == "__main__":
    app.run(port=5057, debug=False)
