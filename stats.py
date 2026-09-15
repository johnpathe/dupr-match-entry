"""Stats & Analytics: turns a club's match history for a given event/league
name into team and player records, streaks, and a few derived "fun facts".
Also has a simple, clearly-labeled matchup predictor.

On the predictor: DUPR does not expose a public prediction endpoint. The
community client library documents `POST /match/{version}/expected-score`,
but every payload shape tried against it returns a generic 400, and watching
DUPR's own "New Match" page fill in two full teams never fires a call to it
either -- there's no live reference to reverse-engineer from. So instead of
guessing at DUPR's real (undisclosed) rating algorithm, predict_matchup()
uses a standard rating-difference win-probability model and calibrates its
"typical point swing" from your own league's actual recorded rating changes,
and says so plainly in its output.
"""
import math

API_BASE = "https://api.dupr.com"
_OUTCOME_FIELD = {"win": "wins", "loss": "losses", "draw": "draws"}


def fetch_club_matches(client, club_id, event_name=None, max_matches=3000):
    """All matches for a club, optionally scoped to one event/league name
    (DUPR's own Matches-tab search box filters the same way). Sorted oldest
    first so streak calculations read left-to-right chronologically."""
    matches = []
    offset = 0
    limit = 200
    filters = {"eventName": event_name} if event_name else {}
    while True:
        r = client.session.post(
            f"{API_BASE}/club/match/v1.0/history",
            json={"clubId": club_id, "filters": filters, "limit": limit,
                  "offset": offset, "sort": {"order": "ASC", "parameter": "MATCH_DATE"}},
            timeout=30,
        )
        r.raise_for_status()
        result = r.json().get("result", {})
        hits = result.get("hits", [])
        matches.extend(hits)
        total = result.get("total", len(matches))
        offset += limit
        if not hits or offset >= total or len(matches) >= max_matches:
            break
    return matches


def _team_key(ids):
    return tuple(sorted(ids))


def _team_name(ids, names):
    return " & ".join(names.get(i, f"#{i}") for i in ids)


def _played_games(team_a, team_b):
    games = []
    for i in range(1, 6):
        a, b = team_a.get(f"game{i}"), team_b.get(f"game{i}")
        if a is not None and b is not None and a >= 0 and b >= 0:
            games.append((a, b))
    return games


def _current_streak(results):
    """results: list of (date, 'win'|'loss'|'draw'). Returns (type, length)
    for the most recent unbroken run, type in {'W','L','D'} or ('', 0)."""
    results = sorted(results, key=lambda r: r[0] or "")
    if not results:
        return "", 0
    last = results[-1][1]
    n = 0
    for _, outcome in reversed(results):
        if outcome == last:
            n += 1
        else:
            break
    return {"win": "W", "loss": "L", "draw": "D"}[last], n


def compute_stats(matches):
    names = {}
    team_stats = {}
    player_stats = {}
    match_rows = []

    for m in matches:
        teams = m.get("teams") or []
        if len(teams) != 2:
            continue
        team_a, team_b = teams
        players_a = [p for p in (team_a.get("player1"), team_a.get("player2")) if p]
        players_b = [p for p in (team_b.get("player1"), team_b.get("player2")) if p]
        if not players_a or not players_b:
            continue
        for p in players_a + players_b:
            names[p["id"]] = p.get("fullName") or f"#{p['id']}"

        ids_a = [p["id"] for p in players_a]
        ids_b = [p["id"] for p in players_b]
        win_a, win_b = bool(team_a.get("winner")), bool(team_b.get("winner"))
        outcome_a = "win" if win_a and not win_b else "loss" if win_b and not win_a else "draw"
        outcome_b = {"win": "loss", "loss": "win", "draw": "draw"}[outcome_a]

        played = _played_games(team_a, team_b)
        points_a = sum(a for a, b in played)
        points_b = sum(b for a, b in played)
        date = m.get("eventDate")

        for ids, outcome, pf, pa in ((ids_a, outcome_a, points_a, points_b),
                                      (ids_b, outcome_b, points_b, points_a)):
            key = _team_key(ids)
            ts = team_stats.setdefault(key, {
                "ids": list(ids), "wins": 0, "losses": 0, "draws": 0,
                "pointsFor": 0, "pointsAgainst": 0, "results": [],
            })
            ts[_OUTCOME_FIELD[outcome]] += 1
            ts["pointsFor"] += pf
            ts["pointsAgainst"] += pa
            ts["results"].append((date, outcome))

            for pid in ids:
                ps = player_stats.setdefault(pid, {
                    "id": pid, "wins": 0, "losses": 0, "draws": 0,
                    "results": [], "ratings": [],
                })
                ps[_OUTCOME_FIELD[outcome]] += 1
                ps["results"].append((date, outcome))

        # rating snapshots (for a "rating trend over this period" stat --
        # more robust than trusting the tiny per-match impact values, which
        # looked like placeholders on club-verified imports)
        for team, players in ((team_a, players_a), (team_b, players_b)):
            impact = team.get("preMatchRatingAndImpact") or {}
            slots = ["Player1", "Player2"][:len(players)]
            for slot, p in zip(slots, players):
                pre = impact.get(f"preMatchDoubleRating{slot}")
                if pre is not None:
                    player_stats.setdefault(p["id"], {
                        "id": p["id"], "wins": 0, "losses": 0, "draws": 0,
                        "results": [], "ratings": [],
                    })["ratings"].append((date, pre))

        if played:
            match_rows.append({
                "date": date,
                "teamA": {"ids": ids_a, "name": _team_name(ids_a, names)},
                "teamB": {"ids": ids_b, "name": _team_name(ids_b, names)},
                "pointsA": points_a, "pointsB": points_b,
                "margin": abs(points_a - points_b),
            })

    # ------------------------------------------------------------ team table
    team_table = []
    for ts in team_stats.values():
        total = ts["wins"] + ts["losses"] + ts["draws"]
        if not total:
            continue
        streak_type, streak_len = _current_streak(ts["results"])
        team_table.append({
            "name": _team_name(ts["ids"], names), "ids": ts["ids"], "matches": total,
            "wins": ts["wins"], "losses": ts["losses"], "draws": ts["draws"],
            "winPct": round(100 * ts["wins"] / total, 1),
            "pointsFor": ts["pointsFor"], "pointsAgainst": ts["pointsAgainst"],
            "avgMargin": round((ts["pointsFor"] - ts["pointsAgainst"]) / total, 1),
            "streakType": streak_type, "streakLen": streak_len,
            "streakLabel": f"{streak_len}{streak_type}" if streak_type else "-",
        })
    team_table.sort(key=lambda t: (-t["winPct"], -t["matches"]))

    # ---------------------------------------------------------- player table
    player_table = []
    for pid, ps in player_stats.items():
        total = ps["wins"] + ps["losses"] + ps["draws"]
        if not total:
            continue
        ratings = sorted(r for r in ps["ratings"] if r[1] is not None)
        rating_trend = round(ratings[-1][1] - ratings[0][1], 3) if len(ratings) >= 2 else None
        streak_type, streak_len = _current_streak(ps["results"])
        player_table.append({
            "id": pid, "name": names.get(pid, f"#{pid}"), "matches": total,
            "wins": ps["wins"], "losses": ps["losses"], "draws": ps["draws"],
            "winPct": round(100 * ps["wins"] / total, 1),
            "ratingTrend": rating_trend,
            "streakType": streak_type, "streakLen": streak_len,
            "streakLabel": f"{streak_len}{streak_type}" if streak_type else "-",
        })
    player_table.sort(key=lambda p: (-p["winPct"], -p["matches"]))

    # ------------------------------------------------------------ fun facts
    facts = []
    if match_rows:
        closest = min(match_rows, key=lambda r: r["margin"])
        blowout = max(match_rows, key=lambda r: r["margin"])
        facts.append({"label": "Closest match",
                      "value": f'{closest["teamA"]["name"]} {closest["pointsA"]}-{closest["pointsB"]} '
                               f'{closest["teamB"]["name"]} ({closest["date"]})'})
        facts.append({"label": "Biggest blowout",
                      "value": f'{blowout["teamA"]["name"]} {blowout["pointsA"]}-{blowout["pointsB"]} '
                               f'{blowout["teamB"]["name"]} ({blowout["date"]})'})
    if team_table:
        iron_duo = max(team_table, key=lambda t: t["matches"])
        facts.append({"label": "Most matches played together",
                      "value": f'{iron_duo["name"]} ({iron_duo["matches"]} matches)'})
        qualified = [t for t in team_table if t["matches"] >= 3]
        if qualified:
            best = qualified[0]
            facts.append({"label": "Best record (min. 3 matches)",
                          "value": f'{best["name"]} — {best["wins"]}-{best["losses"]}-{best["draws"]} ({best["winPct"]}%)'})
        win_streaks = [t for t in team_table if t["streakType"] == "W"]
        if win_streaks:
            hot = max(win_streaks, key=lambda t: t["streakLen"])
            facts.append({"label": "Hottest team right now",
                          "value": f'{hot["name"]} — {hot["streakLen"]} wins in a row'})
    if player_table:
        most_active = max(player_table, key=lambda p: p["matches"])
        facts.append({"label": "Most matches played",
                      "value": f'{most_active["name"]} ({most_active["matches"]} matches)'})
        climbers = [p for p in player_table if p["ratingTrend"] is not None]
        if climbers:
            top = max(climbers, key=lambda p: p["ratingTrend"])
            sign = "+" if top["ratingTrend"] >= 0 else ""
            facts.append({"label": "Biggest rating climb this period",
                          "value": f'{top["name"]} ({sign}{top["ratingTrend"]})'})

    return {
        "matchCount": len(matches),
        "dateRange": [matches[0]["eventDate"], matches[-1]["eventDate"]] if matches else None,
        "teams": team_table,
        "players": player_table,
        "facts": facts,
    }


def get_current_doubles_rating(client, player_id):
    try:
        r = client.session.get(f"{API_BASE}/player/v1.0/{player_id}", timeout=15)
        r.raise_for_status()
        val = (r.json().get("result", {}).get("ratings") or {}).get("doubles")
        return float(val)
    except (Exception, TypeError, ValueError):
        return None


def predict_matchup(client, team_a_ids, team_b_ids, historical_matches):
    """Estimated win probability + rating-point swing for a hypothetical
    Team A vs Team B doubles match. See module docstring: this is NOT DUPR's
    real prediction -- no working endpoint or UI reference for that exists --
    it's a standard rating-difference model, calibrated against this club's
    own recorded rating changes."""
    ratings_a = [get_current_doubles_rating(client, p) for p in team_a_ids]
    ratings_b = [get_current_doubles_rating(client, p) for p in team_b_ids]
    ratings_a = [r for r in ratings_a if r is not None]
    ratings_b = [r for r in ratings_b if r is not None]
    if not ratings_a or not ratings_b:
        return {"error": "Could not fetch current ratings for one or more selected players."}

    avg_a = sum(ratings_a) / len(ratings_a)
    avg_b = sum(ratings_b) / len(ratings_b)
    diff = avg_a - avg_b

    scale = 0.5  # rough "gap for a clear favorite" on DUPR's rating scale
    try:
        prob_a = 1 / (1 + 10 ** (-diff / scale))
    except OverflowError:
        prob_a = 0.0 if diff < 0 else 1.0

    impacts = []
    for m in historical_matches:
        for t in m.get("teams", []):
            impact = t.get("preMatchRatingAndImpact") or {}
            for slot in ("Player1", "Player2"):
                v = impact.get(f"matchDoubleRatingImpact{slot}")
                if v:
                    impacts.append(abs(v))
    k = (sum(impacts) / len(impacts)) if impacts else 0.01

    swing_if_a_wins = round(k * (1 - prob_a) * 2, 4)
    swing_if_a_loses = round(-k * prob_a * 2, 4)

    return {
        "teamA": {"avgRating": round(avg_a, 3), "winProbability": round(prob_a * 100, 1)},
        "teamB": {"avgRating": round(avg_b, 3), "winProbability": round((1 - prob_a) * 100, 1)},
        "ifTeamAWins": {"teamA": swing_if_a_wins, "teamB": -swing_if_a_wins},
        "ifTeamBWins": {"teamA": swing_if_a_loses, "teamB": -swing_if_a_loses},
        "note": ("Estimate only -- DUPR doesn't expose a public prediction API. This uses "
                 "a standard rating-difference model, with the typical point swing "
                 f"(±{k:.4f}) calibrated from {len(impacts)} of your own league's recorded "
                 "rating changes, not DUPR's real (undisclosed) algorithm."),
    }
