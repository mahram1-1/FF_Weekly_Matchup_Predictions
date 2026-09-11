# Gleague Weekly Agent

Weekly power rankings and insights for a Sleeper league. Monte Carlo simulation
with fitted injury behaviour, snapshotted week over week so any report can be
rebuilt exactly.

```
python3 run.py              # current week
python3 run.py 4            # a specific week
python3 run.py 4 --replay   # rebuild wk4 from its frozen snapshot
python3 run.py 4 --claude   # add the LLM versions
```

## Cost

**The pipeline is free.** Sleeper's API, nflverse data and the DynastyProcess
crosswalk are all public and unauthenticated. Everything — projections, the
25,000-week simulation, both markdown documents — is local Python. GitHub Actions
is free for public repos.

The only paid component is `--claude`, which calls the Claude API to rewrite the
facts as prose. A Claude Pro/Max subscription does **not** cover this; paid plans
and the Console are separate products billed separately.

If you don't want API spend, don't pass `--claude`. You lose no analysis — the
deterministic documents contain every number. For the prose versions, paste
`out/facts_wkNN_brief.json` into a Claude chat using the free path in
`WEEKLY_PROMPT.md`.

## Running it weekly

See `WEEKLY_PROMPT.md` for copy-paste prompts. The short version: the container
filesystem resets between chats, so the repo needs to arrive by git clone or
upload — Project knowledge is injected as context, not written to disk, and can't
be executed. Put this on GitHub if you can; `snapshots/` living in git history is
what makes the ± column and `--replay` work.

## Two documents, one facts packet

| File | What it is | Reader |
|---|---|---|
| `out/power_wkNN.md` | League-wide rankings, injury tax, leverage, tier reference | Everyone, once a week |
| `out/matchups_wkNN.md` | Six previews with projections, positional edges, win paths | Whoever's playing |
| `out/facts_wkNN.json` | The audit trail both are built from | You, when someone argues |

`--claude` adds `_claude.md` versions of each with separate system prompts — the
rankings piece is a beat-writer voice, the previews are a preview column. The
matchup prompt receives a trimmed packet so the model isn't reading rankings it
shouldn't reference.

### The matchup doc is four things

Opponents, players to watch, Sleeper projections, and win odds at floor and
ceiling. Nothing else.

**Win odds by outcome.** Because the two totals are near-independent,
P(I win | I score X) is just P(opponent finishes under X) — exact, not estimated.
Read across: *HYPE BEASTS win 5% of the time at their floor, 33% at expectation,
79% at their ceiling.* The useful question is whether a team's ceiling is even
enough, or whether its floor still wins.

**No start/sit advice, in either document.** The substitution plan describes what
the simulation does when a tagged player sits. It is not a recommendation and
isn't rendered in the rankings. The Claude prompts for both documents forbid it
explicitly.

**Positional edges, paths to victory and magic numbers still exist** in
`simulate.py` but are off by default. Set `matchup_detail: "full"` in the run
config to put them back in the facts packet.

**Week-over-week movement.** The rankings table carries a ± column computed
against the prior week's snapshot, so rank changes are reproducible rather than
remembered.

## The power rankings document

Ranked table, then a section per team: rank, name, record, the simulated line,
and bullets.

| Bullet | Basis |
|---|---|
| **Watch** | Leverage swing — how far win odds move between the player's worst and best quarter of outcomes |
| **Not buying** | A skill starter landing inside the ±45% band under 55% of the time, or a real availability cloud |
| **Reached** | Pick number minus Sleeper PPR ADP |
| **Falling** | Positional rank on draft day vs by current rest-of-season projection |
| **Upside** | Ceiling above projection, in points, skill positions only |
| **Late value** | Round 6+ pick who has climbed 8+ spots at his position |
| **The read** | Assembled from league-relative facts: ceiling/floor/volatility extremes, injury tax, all-play, total draft reach |

### Two draft yardsticks that disagree on purpose

**Reach vs market** is pick number minus ADP — a fact about the draft, not proof
the pick was wrong. **Value vs now** is positional rank on draft day against
current projected rank, which moves every week.

They disagree usefully. Allentown's Eagles DEF was a 21-slot reach but has climbed
from 5th to 4th at the position. JosephDuane's Kaleb Johnson went 77 slots *ahead*
of ADP as a value pick and has since fallen from RB46 to RB82. Reporting only one
yardstick would relabel ADP as insight.

### Position-structural flags are suppressed

Every defence lands inside the band around 46% of the time and every kicker around
55%. That is the position, not the pick. Flagging it would list all 24 of them, so
the volatility bullet applies to skill positions only. Availability flags still
apply to everyone. "Watch" prefers skill positions too — a defence can genuinely
swing a week, but "players to watch: the Chargers" is not a sentence. DEF and K
leverage rows stay in the facts packet.

### No roster advice, in either document

The substitution plan describes what the simulation does when a tagged player
sits. It is not a recommendation, isn't rendered in the rankings, and both Claude
prompts forbid start/sit language explicitly.

## League scoring, not stock PPR

Sleeper's projection rows carry a `pts_ppr` convenience field computed with
standard full-PPR. This league has customised scoring, so that field is wrong —
and it's wrong exactly where nobody checks.

| Pos | n | Mean stock | Mean league | Mean Δ | Worst Δ |
|---|---|---|---|---|---|
| QB | 32 | 17.1 | 17.1 | 0.00 | −0.04 |
| RB | 71 | 9.6 | 9.6 | 0.00 | −0.07 |
| WR | 119 | 8.7 | 8.7 | 0.00 | −0.05 |
| TE | 61 | 6.4 | 6.4 | 0.00 | −0.04 |
| K | 32 | 6.4 | 6.1 | −0.34 | −0.54 |
| **DEF** | 32 | **6.8** | **4.6** | **−2.27** | **−4.13** |

Skill positions match to within a tenth of a point — this league runs Sleeper
defaults there, including `pass_int` at −1 rather than the −2 you see on ESPN.

**Defence is a different game.** The league adds a continuous −0.1 per point
allowed on top of the tiered buckets, makes those buckets far harsher
(14–20 pts is −1 here vs +1 stock; 21–27 is −2 vs 0), penalises yardage from 350
up, and adds `tkl_loss` 0.5, `ff` 1.0 and `def_pass_def` 0.25 as bonuses.

Consequences:
- Every DEF projection was **2.3 points too high**, in a slot every team starts.
- The ordering changes. Seattle drops from 3rd to 8th; Las Vegas rises 5th to 3rd.
- The good-to-bad spread widens from 6.0 points to **9.5** — 58% wider.
- Bad defences go **negative**. Arizona projects −0.6, New Orleans −0.3.

Priors are refit on league-scored historical actuals too, so the *shape* is right
and not just the level. Under league scoring DEF volatility explodes:

| DEF tier | Floor | Avg | Ceiling | CV |
|---|---|---|---|---|
| Top | 1.1 | 11.1 | 22.4 | 0.77 |
| Middle | −0.9 | 8.0 | 19.2 | 1.03 |
| Bottom | −3.8 | 5.3 | 15.6 | 1.51 |

A bottom-tier defence has a **negative floor** and a coefficient of variation
above 1.5. In this league a streaming defence is not a low-upside safe play, it's
the single most volatile thing in a lineup.

If the commissioner changes scoring, rebuild:
`python3 -c "from gleague import priors, sleeper; priors.build(settings=sleeper.league(ID)['scoring_settings'])"`

## Ratings are points per week, not z-scores

The composite used to z-score each component. That was wrong. Rest-of-season
roster strength spans about 80 points across the league (4.7 per week) while the
weekly simulation spans about 11 per week. Standardising both to unit variance
gave the 4.7-point spread equal weight with the 11-point one, and produced tables
where 12th place projected higher than 8th.

Now everything is blended in points per week. The rating is a readable
points-per-week estimate you can sanity-check by eye, and the ordering matches the
projections.

## Boom, bust, and neither

You asked whether a player lands within ±24% of his projection most of the time.
**He does not.** Measured on 14,532 player-weeks from 2023-25, joining Sleeper's
Rotowire projection to the actual result:

| Band | Bust | Neither | Boom |
|---|---|---|---|
| ±24% | 39.9% | **27.8%** | 32.2% |
| ±30% | 36.0% | 34.5% | 29.6% |
| ±40% | 29.0% | 45.5% | 25.6% |
| **±45%** | 25.4% | **51.0%** | 23.6% |
| ±50% | 22.2% | 55.8% | 21.9% |

At ±24%, "neither" is the *smallest* of the three buckets and **bust is the most
common single outcome**. The instinct that the middle should dominate is right;
the width was too tight by about half.

Two reasons it's tighter than it feels:

- **Projections run ~9% hot at the median.** Median actual ÷ projection is 0.914,
  so simply matching a projection is already an above-median week. That's what
  pushes bust above boom at every band width.
- The distribution is right-skewed, so the mean sits above the median and a
  symmetric percentage band is not symmetric in probability.

Sanity check on the data: correlation between projection and actual is 0.541 —
a real forecast, not a hindsight-fitted number.

**The band is ±45%, fixed league-wide, and that's deliberate.** Coverage varies
enormously by tier — an elite QB needs only ±27% to reach 50% coverage, a
bottom-third RB needs ±59%, a bottom-third DEF ±64%. Using tier-specific bands
would pin "neither" near 50% for everyone by construction and destroy the signal.
With one common band, **a high "neither" share IS the safety measure**: Herbert
sits at 78%, a streaming defence at 46%. Full tier table in
`data/outcome_bands.json`.

## Layout

```
gleague/
  sleeper.py    league, rosters, matchups, projections        TESTED
  priors.py     fits outcome distributions + correlations     TESTED
  injuries.py   nflverse -> ESPN -> Sleeper resolver          nflverse TESTED, ESPN NOT
  simulate.py   copula + lineup-state Monte Carlo             TESTED
  sources.py    optional multi-source projection blending     ESPN/FP NOT TESTED
  analytics.py  power model, optimal lineup, all-play         TESTED
  facts.py      assembles the facts packet
  writeup.py    markdown render + Claude prose layer
  snapshot.py   week-over-week freeze / replay
data/           priors.json, playrates_2025.json
snapshots/      {season}/wk{NN}/ frozen inputs + manifest
out/            facts_wkNN.json, report_wkNN.md
```

**The rule that matters:** the LLM never computes anything. It receives a finished
facts packet and writes prose. Every number traces back to `out/facts_wkNN.json`.

## Week-over-week repeatability

Injury tags change hourly and projections change daily, so a report you can't
rebuild is a report you can't defend. Every run freezes its exact inputs to
`snapshots/{season}/wk{NN}/` with a manifest recording timestamp and provenance.
`--replay` rebuilds from the snapshot instead of re-fetching. Verified
byte-identical.

The RNG seed is `sha256(league|season|week)`, so identical inputs always give
identical output. The model never changes its mind overnight.

Recommended weekly cadence: run Tuesday after waivers for the power rankings, and
again Sunday morning once inactives are posted. Keep both snapshots.

## How availability is modelled

Old approach, which was wrong: multiply an injured player's score by a Bernoulli.
That scores his slot as ZERO when he sits, as if the manager never subbed. It
punished exactly the teams best insulated against injury and turned the league
table into a ranking of who had the most Questionable tags.

Now: every in/out combination is a LINEUP STATE. For each, the lineup is
re-solved in two passes and the states are drawn in proportion to probability.

1. Re-seat available original starters optimally. RB1 sits, the FLEX running back
   slides up to RB.
2. Fill what's still open from the bench, best projection first.

Pass 1 can't bench a healthy starter — this covers holes, it doesn't second-guess
lineups. Bench players under 50% to play aren't used as cover. If the pool runs
dry the slot goes dark, which is the honest answer for a roster with no depth.

The reported **drop** is the true marginal cost: full lineup total minus lineup
total after re-solving without him. Not his projection.

## Play rates are fitted, not assumed

`data/playrates_2025.json` joins the 2025 official NFL injury report to Sleeper's
`gp` flag over 1,831 fantasy-position player-weeks.

| Designation | Practice | n | P(play) | Points vs own avg |
|---|---|---|---|---|
| none | Full | 810 | 93.8% | 1.00 |
| Out | DNP | 360 | 0% | — |
| Questionable | Limited | 228 | 64.9% | 0.99 |
| none | Limited | 94 | 97.9% | 0.82 |
| Questionable | Full | 85 | 68.2% | 0.80 |
| none | DNP | 72 | 63.9% | 0.93 |
| Questionable | DNP | 44 | 54.5% | — |

Two things worth knowing:

- **Questionable is ~65%, not the 75% I assumed earlier.** The old number was too
  optimistic and it was inflating every team carrying a tag.
- **Practice participation is a weaker signal than the internet claims.**
  Questionable+Full 68% vs Questionable+DNP 55% — a 13-point spread on n=85 and
  n=44. Don't over-read Friday practice reports.

Doubtful is still an assumption (0.15). Only 27 fantasy-position rows in 2025,
too few to fit. Cells like that are marked with `*` in the report.

## How floor and ceiling are calculated

Both are percentiles of the simulated **team total** across 25,000 weeks — the
10th and the 90th. Not a projection minus a fudge factor, and not any single
player's worst case: a team floor is the total in the worst tenth of simulated
weeks, which is many players below average at once, not everyone bottoming out
together.

Player draws come from **tier-specific** outcome pools, not a flat per-position
shape. 5 tiers for QB/RB/WR/TE, 3 for K/DEF, fit on **2023, 2024 and 2025**
actuals. `tier_reference` in the facts packet and the report carries the full
table.

Two team variants are reported side by side. **As it stands** carries each tagged
player's fitted chance of sitting. **If everyone plays** forces every play
probability to 1.0. The gap is the **injury tax**.

Player-level floors in the leverage table are *conditional on him playing* — the
chance he sits is handled by the lineup states, not folded into his own range.

### Which games count

A player forced out **before halftime** never produced a fantasy outcome you
could have planned around, so those weeks are dropped. A player who made it to
**Q3 or Q4** and got hurt there IS a real outcome and stays in the average.

Last-snap quarter is exact — nflverse participation data (offensive players on
every play) joined to play-by-play for the quarter. Not inferred from snap totals.
Applied only to every-down players (median offensive snap share ≥ 40%), so a
backup who legitimately plays two series early isn't mistaken for an injury.

Over three seasons: **181 weeks censored** as first-half exits (1.1%), **444
second-half exits kept**, 15,801 full games.

The earlier snap-share heuristic censored 3.8% — more than three times as many.
It was catching blowout benchings, committee weeks and ejections, not injuries.
Precise quarter data cut the false positives by two-thirds.

### Tier reference (2023–25 actuals)

| Pos | Tier | PPG range | Weeks | Floor | Avg | Ceiling | CV | Under half |
|---|---|---|---|---|---|---|---|---|
| QB | 1/5 | 19.2–25.6 | 367 | 11.6 | 21.4 | 31.4 | 0.35 | 8% |
| QB | 3/5 | 15.1–17.1 | 300 | 8.5 | 16.3 | 25.0 | 0.40 | 9% |
| QB | 5/5 | 3.6–12.4 | 247 | 1.2 | 10.4 | 19.6 | 0.65 | 22% |
| RB | 1/5 | 14.7–24.5 | 726 | 7.8 | 17.7 | 28.5 | 0.46 | 13% |
| RB | 3/5 | 6.7–10.3 | 670 | 1.9 | 8.5 | 16.8 | 0.71 | 26% |
| RB | 5/5 | 3.0–4.8 | 556 | 0.4 | 3.8 | 9.2 | 0.94 | 35% |
| WR | 1/5 | 13.2–23.7 | 1143 | 6.3 | 16.6 | 28.1 | 0.52 | 18% |
| WR | 3/5 | 6.9–9.5 | 1045 | 2.3 | 8.3 | 15.9 | 0.67 | 26% |
| WR | 5/5 | 3.0–5.1 | 818 | 1.3 | 4.2 | 8.7 | 0.75 | 30% |
| TE | 1/5 | 10.4–18.6 | 582 | 4.5 | 12.7 | 22.1 | 0.55 | 19% |
| TE | 5/5 | 3.0–4.1 | 376 | 1.4 | 3.5 | 6.8 | 0.71 | 28% |

Things worth knowing from this table:

- **An elite WR's floor (6.3) is roughly a WR3's average (8.3).** Your best
  receiver gives you a replacement-level week 10% of the time, injury-free.
- **QB1s are the only genuinely safe start.** Floor is 54% of average; every
  other tier-1 skill position is 35–44%.
- **RB volatility explodes at the bottom.** RB5 CV is 0.94 with a 0.4 floor —
  those weeks are near-zero far more often than a projection implies.
- 5 tiers is the right granularity. At 6 the CV progression stops being monotone;
  at 3 the top and bottom of each tier are too different to pool.

## Play rates, fitted on 2023–25

Joining three seasons of the official NFL injury report to Sleeper's `gp` flag,
over roughly 5,500 fantasy-position player-weeks:

| Designation | Practice | n | P(play) | Points vs own avg |
|---|---|---|---|---|
| none | Full | 2255 | 93.7% | 0.99 |
| Out | DNP | 887 | 0.1% | — |
| Questionable | Limited | 767 | 62.5% | 0.93 |
| none | Limited | 359 | 95.3% | 0.94 |
| Questionable | Full | 319 | 73.4% | 0.87 |
| none | DNP | 212 | 72.2% | 0.86 |
| Questionable | DNP | 198 | 46.5% | 0.83 |
| Doubtful | DNP | 92 | 0.0% | — |
| Doubtful | Limited | 44 | 2.3% | — |

Pooled: Questionable **63.0%** (n=1312), Doubtful **0.6%** (n=154), Out **0.1%**.

Two corrections to earlier single-season fits:

- **Doubtful is 0.6%, not the 15% previously assumed.** Doubtful players
  essentially never play. Treat the designation as Out.
- **Practice participation IS a real signal.** Questionable+Full 73.4% vs
  Questionable+DNP 46.5% — a 27-point spread. The single-season fit showed only
  13 points and I called it weak; that was small-sample noise. Three seasons
  separates the cells cleanly.

Practice detail requires the season injury file, which 404s until Week 1 posts.
Until then every Questionable player gets the pooled 63.0%.

## Data sources

| Source | Auth | Status |
|---|---|---|
| Sleeper league + projections (Rotowire) | none | works |
| Sleeper 2025 weekly actuals | none | works |
| nflverse official NFL injury report | none | works; `injuries_2026.csv` 404s until Week 1 posts |
| DynastyProcess ID crosswalk | none | works, 167/168 non-DEF players mapped |
| ESPN injuries endpoint | none | **written, untested** — sandbox blocked espn.com |
| ESPN / FantasyPros projections | none / free key | **written, untested** |

Sleeper's `company=` projection parameter is a dead end: every provider other than
`rotowire` returns stub records with empty stats. Verified 2026-09-07.

## Known limitations

1. **ESPN is unverified.** Both the injuries and projections adapters are written
   from the documented shape of those endpoints but never executed. Run them and
   check the output before the first real send. They fail soft (return `{}`) so the
   pipeline degrades rather than crashes, which also means a silent failure looks
   like "no injuries this week" — check `method.injury_sources` in the facts packet.
2. **nflverse has no 2026 file yet**, so Week 1 runs on Sleeper's bare tag with no
   practice detail. Every Questionable player gets the same pooled 64.7%. Practice
   granularity arrives once the season file publishes.
3. **Doubtful, PUP, IR and Sus rates are assumed.** Marked `*` in the report.
4. **Bench substitutes get a simple availability draw**, not their own state
   enumeration. Slightly understates variance for teams whose cover is also banged
   up.
5. **Priors are 2025-only.** Blend in-season actuals so a player's own shape starts
   overriding his tier's around Week 6.
6. **No beat-reporter context.** Sleeper's `injury_notes` is often just "Surgery".
   ESPN's comment field is the fix, once verified.

## Scheduling

```yaml
on:
  schedule:
    - cron: "0 13 * * 2"     # Tue 9am ET, post-waivers
    - cron: "0 15 * * 0"     # Sun 11am ET, post-inactives
jobs:
  brief:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: pip install numpy scipy pyarrow
      - run: python3 run.py --claude
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - run: |
          git add snapshots out && git commit -m "week auto" && git push
```

Committing `snapshots/` is the point — it's the audit trail.
Sleeper has no public write API, so delivery is Discord/Slack webhook or paste.
