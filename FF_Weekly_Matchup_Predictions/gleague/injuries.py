"""Injury status resolution, best source first, with provenance recorded.

Sleeper's own injury_status is a tag with no context ("Questionable", body part
"Undisclosed"). That is not enough to decide whether to simulate a player. This
module layers better sources on top and records which one answered, because a
report you can't reproduce is a report you can't defend in the group chat.

Source order:
  1. nflverse official NFL injury report -- game designation AND practice
     participation (Wed/Thu/Fri). This is the actual league source of truth.
     Published as a season CSV; 2026 appears once Week 1 is in the books.
  2. ESPN public injuries endpoint -- updated intraday, carries a comment field
     with beat-reporter context. UNTESTED here (sandbox blocks espn.com).
  3. Sleeper's tag -- always available, least informative. Fallback only.

PLAY RATES ARE FITTED, NOT ASSUMED. data/playrates_2025.json was built by joining
the 2025 official injury report to Sleeper's weekly gp flag over 1,831
fantasy-position player-weeks.
"""
from __future__ import annotations
import csv, io, json, time, urllib.request, pathlib, functools

DATA = pathlib.Path(__file__).parent.parent / "data"
CACHE = pathlib.Path(__file__).parent.parent / ".cache"
CACHE.mkdir(exist_ok=True)

NFLVERSE = ("https://github.com/nflverse/nflverse-data/releases/download/"
            "injuries/injuries_{season}.csv")
ESPN_INJ = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"

# Fallback when we have a game designation but no practice detail. Pooled across
# practice states from the same fit. Doubtful is an ASSUMPTION -- only 27 rows in
# the 2025 file for fantasy positions, too few to fit.
BY_DESIGNATION = {
    "Questionable": {"p_play": 0.630, "haircut": 0.90, "n": 1312, "fitted": True},
    "Doubtful":     {"p_play": 0.006, "haircut": 0.41, "n": 154,  "fitted": True},
    "Out":          {"p_play": 0.001, "haircut": None, "n": 1068, "fitted": True},
    "IR":           {"p_play": 0.0,   "haircut": None, "n": 0,   "fitted": False},
    "PUP":          {"p_play": 0.0,   "haircut": None, "n": 0,   "fitted": False},
    "NA":           {"p_play": 0.0,   "haircut": None, "n": 0,   "fitted": False},
    "Sus":          {"p_play": 0.0,   "haircut": None, "n": 0,   "fitted": False},
    "COV":          {"p_play": 0.5,   "haircut": 0.90, "n": 0,   "fitted": False},
}


@functools.lru_cache(maxsize=1)
def _playrates() -> dict:
    """Fitted cells from data/priors.json, built over 2023-2025."""
    f = DATA / "priors.json"
    return json.loads(f.read_text()).get("play_rates", {}) if f.exists() else {}


def _practice_bucket(s: str) -> str:
    s = (s or "").lower()
    if "did not" in s: return "DNP"
    if "limited" in s: return "LIM"
    if "full" in s:    return "FULL"
    return "NONE"


# ------------------------------------------------------------------ source 1
def nflverse_report(season: str, week: int) -> dict:
    """gsis_id -> {designation, practice, source}. Returns {} before the season
    file is published (it 404s until Week 1 results exist)."""
    f = CACHE / f"nflverse_inj_{season}.csv"
    if not f.exists() or time.time() - f.stat().st_mtime > 3600:
        try:
            with urllib.request.urlopen(NFLVERSE.format(season=season), timeout=60) as r:
                f.write_bytes(r.read())
        except Exception as e:
            print(f"  [injuries] nflverse {season} unavailable ({e}); falling back")
            return {}
    out = {}
    for row in csv.DictReader(io.StringIO(f.read_text(errors="replace"))):
        if row.get("season_type") != "REG" or int(row.get("week", 0)) != week:
            continue
        out[row["gsis_id"]] = {
            "designation": (row.get("report_status") or "").strip() or None,
            "practice": _practice_bucket(row.get("practice_status")),
            "injury": row.get("report_primary_injury") or row.get("practice_primary_injury"),
            "source": "nflverse_official",
        }
    return out


# ------------------------------------------------------------------ source 2
def espn_injuries() -> dict:
    """espn_id -> {designation, comment, source}. UNTESTED -- verify before trusting.
    ESPN's comment field is where the beat-reporter context lives, which is the
    thing Sleeper's 'Undisclosed' body part never gives you."""
    try:
        req = urllib.request.Request(ESPN_INJ, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception as e:
        print(f"  [injuries] espn unavailable ({e})")
        return {}
    out = {}
    for team in data.get("injuries", []):
        for it in team.get("injuries", []):
            ath = it.get("athlete") or {}
            aid = str(ath.get("id") or "")
            if not aid:
                continue
            out[aid] = {
                "designation": (it.get("status") or "").strip() or None,
                "comment": ((it.get("details") or {}).get("detail")
                            or it.get("longComment") or it.get("shortComment") or "")[:300],
                "returnDate": (it.get("details") or {}).get("returnDate"),
                "source": "espn",
            }
    return out


# ------------------------------------------------------------------ resolver
def resolve(player_ids, season: str, week: int, sleeper_players: dict,
            crosswalk: dict, use_espn=True) -> dict:
    """player_id -> {designation, practice, p_play, haircut, source, fitted, note}

    Every player gets an entry so the simulator never has to guess. Players with
    no injury signal anywhere come back p_play=1.0, source='none'.
    """
    nfl = nflverse_report(season, week)
    espn = espn_injuries() if use_espn else {}
    out = {}
    for pid in player_ids:
        sp = sleeper_players.get(pid) or {}
        xw = crosswalk.get(pid) or {}
        rec = {"designation": None, "practice": None, "source": "none",
               "note": "", "injury": None}

        gsis = xw.get("gsis_id")
        if gsis and gsis in nfl and nfl[gsis]["designation"]:
            rec.update(nfl[gsis])
        elif (eid := xw.get("espn_id")) and eid in espn and espn[eid]["designation"]:
            e = espn[eid]
            rec.update({"designation": e["designation"], "source": "espn",
                        "note": e.get("comment", "")})
        elif sp.get("injury_status"):
            rec.update({"designation": sp["injury_status"], "source": "sleeper",
                        "injury": sp.get("injury_body_part"),
                        "note": sp.get("injury_notes") or ""})

        d = rec["designation"]
        if not d:
            rec.update({"p_play": 1.0, "haircut": 1.0, "fitted": True})
        else:
            cell = None
            if rec.get("practice") and rec["practice"] != "NONE":
                cell = _playrates().get(f"{d}|{rec['practice']}")
            base = BY_DESIGNATION.get(d, {"p_play": 0.6, "haircut": 0.9,
                                          "n": 0, "fitted": False})
            if cell and cell["n"] >= 100:
                rec.update({"p_play": cell["p_play"],
                            "haircut": cell["haircut"] or base["haircut"] or 0.9,
                            "fitted": True, "rate_n": cell["n"]})
            else:
                rec.update({"p_play": base["p_play"],
                            "haircut": base["haircut"] or 1.0,
                            "fitted": base["fitted"], "rate_n": base["n"]})
        out[pid] = rec
    return out
