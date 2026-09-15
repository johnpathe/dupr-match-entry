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

Submission modes
-----------------
DUPR has two ways to record a match:
  - "fallback": PUT /match/v1.0/save, one call per match. This is a normal
    player-reported match -- it needs the other players to confirm the score
    before it counts.
  - "verified": PUT /club/{clubId}/match/verified/v1.0/save/csv/add, a single
    multipart CSV upload scoped to a club. This is what DUPR's own "Import
    Matches" button (Club Overview -> Matches) uses, and it records matches
    as already-verified -- no player confirmation needed. It requires the
    logged-in user to hold CLUB_MATCH "ADD" permission on that club (checked
    live via /club/{clubId}/roles/v1.0/permission, the same check DUPR's own
    site makes), which this app checks before offering it.
"""
import csv
import io
import json
import subprocess
import sys
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from dupr_session import make_client
import stats as stats_mod

HERE = Path(__file__).parent
ROSTER_PATH = HERE / "roster.json"
ROSTER_EXAMPLE_PATH = HERE / "roster.example.json"
API_BASE = "https://api.dupr.com"

if not ROSTER_PATH.exists() and ROSTER_EXAMPLE_PATH.exists():
    import shutil
    shutil.copy(ROSTER_EXAMPLE_PATH, ROSTER_PATH)

app = Flask(__name__, static_folder=None)

CSV_HEADER = [
    "matchType", "event", "date",
    "playerA1", "playerA1DuprId", "playerA1ExternalId",
    "playerA2", "playerA2DuprId", "playerA2ExternalId",
    "playerB1", "playerB1DuprId", "playerB1ExternalId",
    "playerB2", "playerB2DuprId", "playerB2ExternalId",
    "teamAGame1", "teamBGame1", "teamAGame2", "teamBGame2",
    "teamAGame3", "teamBGame3", "teamAGame4", "teamBGame4",
    "teamAGame5", "teamBGame5", "location", "scoreType",
]


def load_roster():
    return json.loads(ROSTER_PATH.read_text())


def save_roster(data):
    ROSTER_PATH.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------- DUPR calls

def get_current_profile(client):
    """Returns the logged-in user's profile dict, or None if not authenticated."""
    try:
        r = client.session.get(f"{API_BASE}/user/v1.0/profile", timeout=15)
    except Exception:
        return None
    if r.status_code in (401, 403):
        return None
    try:
        r.raise_for_status()
        return r.json().get("result")
    except Exception:
        return None


def get_club_permission(client, club_id, user_id):
    """Returns DUPR's own permission-check result for this user on this club,
    the same call the DUPR website makes before letting someone submit club
    matches. None on any failure."""
    try:
        r = client.session.post(
            f"{API_BASE}/club/{club_id}/roles/v1.0/permission",
            json={"userId": user_id}, timeout=15,
        )
        if r.status_code != 200:
            return None
        return r.json().get("result")
    except Exception:
        return None


def get_submission_mode(client, roster):
    """Decide whether this club/user combo can use the no-confirmation
    "verified" club import, or has to fall back to per-match player-reported
    submission. Mirrors the permission check DUPR's own site performs."""
    club_id = roster.get("clubId")
    if not club_id:
        return {"mode": "fallback", "reason": "No club ID set in Season Setup."}

    profile = get_current_profile(client)
    if not profile:
        return {"mode": "fallback", "reason": "Not logged in to DUPR."}

    perm = get_club_permission(client, club_id, profile.get("id"))
    if not perm:
        return {"mode": "fallback",
                "reason": f"Could not check permissions for club {club_id}."}

    can_add = "ADD" in (perm.get("permissions") or {}).get("CLUB_MATCH", [])
    if can_add:
        return {"mode": "verified", "role": perm.get("role"), "clubId": club_id}
    return {"mode": "fallback",
            "reason": f'Your role on this club ("{perm.get("role")}") does not '
                      f"have permission to add club matches."}


# ------------------------------------------------------------- payload build

def build_match_payload(event, location, date, club_id, team1_ids, team2_ids, games):
    """games: list of [team1_score, team2_score], 1-5 entries. The
    player-reported (fallback) JSON shape for PUT /match/v1.0/save."""
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


def player_info(players_by_id, id_):
    p = players_by_id.get(int(id_)) if id_ else None
    return (p["name"], p.get("duprId", "")) if p else ("", "")


def build_csv_text(event, location, date, players_by_id, matches_req):
    """The same 27-column format as DUPR's own Import Matches template."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_HEADER)
    for m in matches_req:
        t1 = [int(x) for x in m["team1"]]
        t2 = [int(x) for x in m["team2"]]
        games = [g for g in m["games"] if g[0] != "" and g[1] != ""]
        a1n, a1d = player_info(players_by_id, t1[0])
        a2n, a2d = player_info(players_by_id, t1[1]) if len(t1) > 1 else ("", "")
        b1n, b1d = player_info(players_by_id, t2[0])
        b2n, b2d = player_info(players_by_id, t2[1]) if len(t2) > 1 else ("", "")
        game_cells = []
        for i in range(5):
            game_cells += [games[i][0], games[i][1]] if i < len(games) else ["", ""]
        w.writerow([
            "D" if (len(t1) > 1 or len(t2) > 1) else "S", event, date,
            a1n, a1d, "", a2n, a2d, "", b1n, b1d, "", b2n, b2d, "",
            *game_cells, location, "SIDEOUT",
        ])
    return buf.getvalue()


def _build_from_request(body, roster):
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
    return payloads, event, location, date


# ------------------------------------------------------------------- routes

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


@app.route("/api/status")
def status():
    client = make_client()
    profile = get_current_profile(client)
    result = {"authenticated": profile is not None}
    if not profile:
        return jsonify(result)

    result["user"] = {"id": profile.get("id"), "fullName": profile.get("fullName")}
    roster = load_roster()
    result["submission"] = get_submission_mode(client, roster)
    return jsonify(result)


_relogin_proc = None


@app.route("/api/relogin", methods=["POST"])
def relogin():
    global _relogin_proc
    already_running = _relogin_proc is not None and _relogin_proc.poll() is None
    if not already_running:
        _relogin_proc = subprocess.Popen(
            [sys.executable, str(HERE / "get_token.py")], cwd=str(HERE)
        )
    return jsonify({"started": not already_running, "alreadyRunning": already_running})


_stats_cache = {"key": None, "matches": None}


def get_club_matches(client, club_id, event_name, force=False):
    """Cached by (club_id, event_name) so /api/predict doesn't have to
    re-fetch the whole match history on every call -- only /api/stats
    (the "Refresh Stats" button) forces a live re-fetch."""
    key = (club_id, event_name)
    if force or _stats_cache["key"] != key or _stats_cache["matches"] is None:
        _stats_cache["matches"] = stats_mod.fetch_club_matches(client, club_id, event_name)
        _stats_cache["key"] = key
    return _stats_cache["matches"]


@app.route("/api/stats")
def get_stats():
    roster = load_roster()
    club_id = roster.get("clubId")
    if not club_id:
        return jsonify({"error": "Set a Club ID in Season Setup first."}), 400
    client = make_client()
    try:
        matches = get_club_matches(client, club_id, roster.get("eventName"), force=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    return jsonify(stats_mod.compute_stats(matches))


@app.route("/api/predict", methods=["POST"])
def predict():
    body = request.get_json(force=True)
    roster = load_roster()
    club_id = roster.get("clubId")
    client = make_client()
    try:
        matches = get_club_matches(client, club_id, roster.get("eventName")) if club_id else []
    except Exception:
        matches = []
    try:
        team_a = [int(x) for x in body["teamA"] if x]
        team_b = [int(x) for x in body["teamB"] if x]
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "Pick at least one player for each team."}), 400
    if not team_a or not team_b:
        return jsonify({"error": "Pick at least one player for each team."}), 400
    return jsonify(stats_mod.predict_matchup(client, team_a, team_b, matches))


@app.route("/api/search_players")
def search_players():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    try:
        r = make_client().session.post(
            f"{API_BASE}/player/v1.0/search",
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


@app.route("/api/preview", methods=["POST"])
def preview():
    body = request.get_json(force=True)
    roster = load_roster()
    try:
        payloads, event, location, date = _build_from_request(body, roster)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    players_by_id = {p["id"]: p for p in roster["players"]}
    csv_text = build_csv_text(event, location, date, players_by_id, body["matches"])
    client = make_client()
    mode = get_submission_mode(client, roster)
    return jsonify({"payloads": payloads, "csv": csv_text, "submission": mode})


@app.route("/api/submit", methods=["POST"])
def submit():
    body = request.get_json(force=True)
    roster = load_roster()
    client = make_client()
    mode = get_submission_mode(client, roster)

    try:
        payloads, event, location, date = _build_from_request(body, roster)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    if mode["mode"] == "verified":
        players_by_id = {p["id"]: p for p in roster["players"]}
        csv_text = build_csv_text(event, location, date, players_by_id, body["matches"])
        try:
            r = client.session.put(
                f"{API_BASE}/club/{mode['clubId']}/match/verified/v1.0/save/csv/add",
                params={"dateFormat": "yyyy-MM-dd"},
                files={"request": ("matches.csv", csv_text, "text/csv")},
                timeout=60,
            )
            ok = r.status_code < 300
            try:
                data = r.json()
            except Exception:
                data = {"raw": r.text[:500]}
            return jsonify({"mode": "verified", "ok": ok, "status": r.status_code,
                             "response": data, "matchCount": len(payloads)})
        except Exception as e:
            return jsonify({"mode": "verified", "ok": False, "error": str(e)})

    # fallback: one player-reported PUT per match, needs opponent confirmation
    results = []
    for p in payloads:
        try:
            r = client.session.put(f"{API_BASE}/match/v1.0/save", json=p, timeout=30)
            ok = r.status_code < 300
            try:
                data = r.json()
            except Exception:
                data = {"raw": r.text[:300]}
            results.append({"ok": ok, "status": r.status_code, "response": data, "sent": p})
        except Exception as e:
            results.append({"ok": False, "error": str(e), "sent": p})
    return jsonify({"mode": "fallback", "reason": mode.get("reason"), "results": results})


if __name__ == "__main__":
    app.run(port=5057, debug=False)
