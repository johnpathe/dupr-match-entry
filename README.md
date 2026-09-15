# DUPR Match Entry

A local tool for entering weekly pickleball match results to
[DUPR](https://dupr.com) without retyping the same event, location, and
player roster every time. Built around
[offsetkeyz/dupr-api-client](https://github.com/offsetkeyz/dupr-api-client).

## Quick start

Double-click **`Start Match Entry App.bat`**. It starts the local server and
opens the app in your browser at <http://127.0.0.1:5057>.

(First time only — see [First-time setup](#first-time-setup) below to install
dependencies and log in.)

## Using the app

- If you're not logged in (or your session expired), a red banner appears at
  the top with a **Log in with browser** button — click it, log in to DUPR in
  the Chromium window that opens, and the app picks it back up automatically.
- **Season Setup** (top card): event name, location, club ID, and your roster
  of regular players. Set this up once per season/competition. The player
  search shows DUPR ID + rating so you can tell same-named players apart —
  DUPR has some duplicate/unclaimed profiles (more than one profile can exist
  under the same name).
- **This Week**: pick the date, then click **+ Add Match** for each match —
  it jumps focus straight to the new match's first player field, so a whole
  week can be entered from the keyboard without reaching for the mouse. Each
  match has 2 players per team (Team A / Team B), 1–5 games, and leaving a
  team's 2nd player as "— none —" makes it singles.
- **Preview** builds the exact payload that would be sent to DUPR and shows
  it — this never contacts DUPR.
- **Download CSV** produces a file in DUPR's own "Import Matches" format
  (Club Overview → Matches → Import Matches → Download Template), in case
  you'd rather upload through DUPR's website yourself instead of using
  Submit here.
- **Submit to DUPR** sends the matches for real, after a confirmation dialog.
  A badge next to "Preview & Submit" shows which of the two ways DUPR will
  record them (see below) — results (saved / failed, with DUPR's response)
  show below the buttons.

DUPR has no sandbox/staging environment (checked — no `staging`/`uat`/`sandbox`
subdomain resolves at all), so **Preview** and **Download CSV** are the safe
ways to check what would be sent before anything goes live.

## Verified (no-confirmation) vs. player-reported matches

DUPR has two different ways a match gets recorded, and this app checks which
one you can use — the same permission check DUPR's own site makes — before
every Preview and Submit:

- **Verified** — a single upload scoped to a club (this is what DUPR's own
  "Import Matches" button does). It counts immediately; no player needs to
  confirm the score. Requires the logged-in user to hold `CLUB_MATCH: ADD`
  permission on the configured club ID (typically an Organizer/Director role).
- **Player-reported (fallback)** — one submission per match, DUPR's normal
  "New Match" flow. Each match sits pending until the other player(s) confirm
  it. If your role or the configured club ID doesn't qualify for verified
  submission, the app **falls back to this automatically** and tells you why
  (visible in the mode badge/hint and in the Preview text), rather than
  silently submitting matches that then need confirmation you didn't expect.

## First-time setup

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe get_token.py
```

The last command opens a Chromium window on `dashboard.dupr.com` — log in
**in that window**. It reads the session cookie, writes it to a local `.env`
file, and closes the browser. The token lasts ~30 days; rerun `get_token.py`
when it expires (it'll skip the login, since `.pw-profile/` stays signed in).

Then copy `roster.example.json` to `roster.json` and fill in your own event
name, location, club ID, and roster (or just use the Season Setup card in the
app — it writes the same file). `match_app.py` also creates `roster.json` from
the example automatically the first time you run it, if it isn't there yet.

## Your roster stays local — it's never in this repo

`roster.json` (real player names + DUPR IDs) is listed in `.gitignore` and is
never committed. Only `roster.example.json` — a generic placeholder with fake
players — is tracked, so the repo can be public/shared without exposing
anyone's name or DUPR ID. If you fork or clone this, your own `roster.json`
stays on your machine the same way.

## Files

| File | What it is |
|------|------------|
| `Start Match Entry App.bat` | Double-click to start the server and open the app |
| `match_app.py` / `match_app.html` | The match-entry web app (Flask backend + single-page frontend) |
| `roster.example.json` | Generic template — copy to `roster.json` and fill in your own roster |
| `roster.json` | **Not tracked in git.** Your real event name, location, club ID, and players |
| `get_token.py` | Logs in via a real browser once, saves your DUPR session token |
| `dupr_session.py` | `make_client()` — a configured `DUPRClient` (auth + TLS fixes below) |
| `example.py` | Minimal example: prints your profile and ratings |
| `dupr-csv-import-template.csv` | DUPR's own CSV import template, for reference |
| `requirements.txt` | Python dependencies |
| `.env` | Your session token (gitignored — never commit this) |
| `.pw-profile/` | Saved browser login for `get_token.py` (gitignored) |

## How match submission works

Both request shapes below were reverse-engineered by filling in DUPR's real
UI in a browser with the actual network request **intercepted and
faked-successful** (via Playwright's `route()`), so nothing was ever really
submitted while figuring either of these out.

**Verified** — `PUT /club/{clubId}/match/verified/v1.0/save/csv/add?dateFormat=yyyy-MM-dd`,
a `multipart/form-data` upload with one field, `request`, containing a CSV in
DUPR's own 27-column Import Matches format (see `dupr-csv-import-template.csv`).
This is the exact call DUPR's own "Import Matches" button makes.

**Player-reported (fallback)** — one `PUT /match/v1.0/save` per match:
```json
{
  "event": "...", "eventDate": "YYYY-MM-DD", "location": "...",
  "matchType": "SIDE_ONLY", "format": "DOUBLES",
  "team1": {"game1": 11, "game2": -1, "game3": -1, "game4": -1, "game5": -1,
            "player1": <id>, "player2": <id_or_null>, "winner": true},
  "team2": {"...": "same shape"}
}
```
Singles (`player2: null`) is untested live but low-risk since the source
league is mostly doubles.

Both the club-permission check (`POST /club/{clubId}/roles/v1.0/permission`)
and the auth check (`GET /user/v1.0/profile`) are the same calls DUPR's own
site makes — `match_app.py`'s `get_submission_mode()` / `get_current_profile()`
just call them directly before deciding how to submit.

## Two things that made this non-obvious

1. **Auth is by cookie, not Bearer.** DUPR's current API (`api.dupr.com`) reads
   the `__Host-dupr_at` cookie. `dupr_session.py` sets it on the client's
   `requests` session instead of using the library's `bearer_token` arg (which
   sends an `Authorization` header the API rejects). The legacy
   `backend.mydupr.com` base URL from the library's docs no longer works.

2. **This machine sits behind a TLS-inspecting proxy.** Python's bundled CA
   bundle fails cert validation against DUPR's certs. `dupr_session.py` calls
   `truststore.inject_into_ssl()` to use the Windows cert store instead — drop
   that call if you run this somewhere without that constraint.

## Manual token fallback

If `get_token.py` breaks: log in at <https://dashboard.dupr.com>, open DevTools
→ Application → Cookies → `https://api.dupr.com`, copy the `__Host-dupr_at`
value into `.env` as `DUPR_BEARER_TOKEN=...`.

## Using the client library directly

```python
from dupr_session import make_client

client = make_client()
client.user.get_profile()
client.players.search_players(query="Jane Doe")
client.matches.get_pending_matches()
```

Resources: `client.user`, `.players`, `.matches`, `.clubs`, `.events`,
`.brackets`, `.admin` — see the [upstream repo](https://github.com/offsetkeyz/dupr-api-client)
for the full method list (note: some endpoints there are stale against DUPR's
current API — see point 1 above).
