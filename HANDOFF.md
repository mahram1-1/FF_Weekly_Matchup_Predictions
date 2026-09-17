# HANDOFF

Context for whoever picks this up. `CLAUDE.md` is the operational summary; this
is the reasoning behind it, including the things that were measured and turned
out differently than expected.

## Where it stands

Working end to end and running in production. A manual GitHub Actions trigger
builds the week and commits the results. Both confirmers pass, 23 checks.

The owner is on a Claude Pro plan and does not want API spend. Nothing in the
pipeline calls a paid API. Keep it that way.

## What was measured, and what it overturned

Every number below came from fitting real data, and several contradicted the
assumption they replaced. This is the section to read before "improving"
something.

**League scoring is not standard PPR.** Sleeper's projection rows carry a
convenience `pts_ppr` field computed with stock full-PPR. Skill positions match
this league within 0.1 of a point, but defence differs by **−2.3 points per
start**, the good-to-bad DEF spread widens from 6.0 to 9.5 points, the ordering
changes (Seattle 3rd → 8th), and bad defences project **negative**. Every DEF
projection was wrong in a slot all twelve teams start.

**Distribution shape is tier-dependent, steeply.** Coefficient of variation for a
top-third RB is 0.50; for a bottom-third RB, 0.92. A flat per-position number
misprices both ends. Pools are 5 tiers for skill positions, 3 for K/DEF, fit on
2023-25 outcome *ratios* (actual ÷ that player's own season mean) and rescaled to
the week's projection. Projection supplies location, history supplies shape.

**Correlations are not where intuition puts them.** QB↔same-team WR is +0.30 and
QB↔opposing DEF is −0.34, but WR↔WR on the same team is **+0.02** — target
competition cancels game environment. Under league scoring DEF↔same-team K rose
to +0.44, because points-allowed scoring is game-script driven.

**Removing mid-game injuries barely moves the floor.** Exits are detected exactly,
from nflverse participation joined to play-by-play for the quarter. Only 1.1% of
player-weeks are first-half exits. Censoring them leaves an elite WR's 10th
percentile unchanged at 35% of his average — bad weeks are game script and
touchdown variance, not injury. It matters only for low-usage RBs (floor 0.10 →
0.33). An earlier snap-share heuristic censored 3.8%, more than three times too
many, because it caught blowout benchings and ejections.

**Questionable is 63%, not 75%.** Fitted by joining three seasons of the official
NFL injury report to Sleeper's `gp` flag. **Doubtful is 0.6%** — treat it as Out;
an earlier 15% assumption was badly wrong. Practice participation *is* a real
signal (Questionable+Full 73.4% vs Questionable+DNP 46.5%); a single-season fit
showed only a 13-point spread and that conclusion was small-sample noise.

**±24% of projection is not the dominant outcome.** Measured on 14,532
player-weeks: it captures 27.8%, the *smallest* of the three buckets, with bust
the most common single outcome at 39.9%. Two causes — projections run ~9% hot at
the median (median actual ÷ projection = 0.914), and the distribution is
right-skewed so a symmetric percentage band is not symmetric in probability.
±45% is where "neither" first clears 50%.

## Bugs found and fixed, so they are not reintroduced

- **Availability as a multiplier** scored an injured starter's slot as zero with
  no replacement, punishing exactly the teams best insulated against injury. Now
  every in/out combination is a lineup state, re-solved in two passes: available
  starters re-seated optimally first (a FLEX back slides up to RB), then the
  bench fills what is open.
- **Draft commentary ignored the live roster** and was naming players who had
  been cut. Now filtered, with cuts and waiver adds surfaced as their own
  buckets.
- **Two classifiers disagreed about the same player.** `verdict.py` was added
  alongside `draft.commentary`'s sentiment buckets, and eight players appeared as
  both praise and criticism. Fixed by making `verdict.py` the only source of
  sentiment.
- **Z-scored ratings** produced tables where 12th place projected higher than
  8th.

## Unverified and assumed

- **The ESPN injuries adapter returns HTTP 403.** It fails soft, returning `{}`,
  which looks identical to "no injuries this week". Always check
  `method.injury_sources` in the facts packet. Possibly needs a browser-like
  header, possibly geo-gated. Unresolved.
- **The ESPN and FantasyPros projection adapters in `sources.py` have never been
  executed.** They are written to the documented endpoint shapes, nothing more.
- **`injuries_2026.csv` on nflverse** may not exist yet. Until it does, every
  Questionable player gets the pooled 63.0% with no practice detail.
- **Doubtful, PUP, IR and Sus rates** are partly assumed; marked `*` in output.
- **`weekly_sigma = 26`** in the win-probability helper is a literature estimate,
  never refit to this league.
- **Sleeper's `company=` projection parameter is a dead end.** Every provider
  other than `rotowire` returns stub records with empty stats.

## Open work, roughly in value order

1. **Blend in-season actuals into the priors** around Week 6 so a player's own
   shape starts overriding his tier's. Currently 2023-25 only.
2. **Resolve or remove the ESPN adapter.** A silently failing source is worse
   than an absent one.
3. **Refit `weekly_sigma`** from this league's realised scores once ~6 weeks
   exist.
4. **Tier-boundary jumpiness.** Tier is assigned from the current week's
   projection, so a player near a boundary hops pools and his floor/ceiling look
   unstable for no real reason. Consider hysteresis or interpolating between
   adjacent pools.
5. **A second projection source.** Everything currently rests on Rotowire, whose
   median runs 9% hot. FantasyPros consensus would be the highest-value addition
   because it is already an average of many analysts.
6. **Snapshot overwrite.** Re-running a week replaces its frozen inputs. Fine
   while runs are manual and deliberate; would need a guard if scheduling returns.

## Things that are working and should be left alone

The simulation core, the scoring engine, the priors fitting, the snapshot/replay
mechanism (verified byte-identical), and both confirmers. The validation layer in
particular has already caught two real bugs that would have shipped.
