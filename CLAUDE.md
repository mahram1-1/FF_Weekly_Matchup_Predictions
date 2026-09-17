# CLAUDE.md

Project instructions for Claude Code. Read this before changing anything.

## What this is

A weekly fantasy football report generator for a 12-team full-PPR Sleeper league
(`league_id` in `config.yaml`). It simulates every team's week 25,000 times and
produces three documents plus a validation report.

```
python3 run.py            # current NFL week
python3 run.py 3          # a specific week
python3 run.py 3 --replay # rebuild from the frozen snapshot, byte-identical
```

Outputs land in `out/`: `power_wkNN.md`, `matchups_wkNN.md`, `share_wkNN.txt`
(plain text for the league chat), `validation_wkNN.md`, and the facts packets.

## The one rule

**The LLM never does arithmetic.** Everything is computed deterministically into
`out/facts_wkNN.json`; the prose layer receives that packet and writes about it.
Every number in every document traces back to the packet. If you add a feature
that has a model compute, derive or estimate a number, you have broken the
project's core guarantee.

## Module map

| File | Owns |
|---|---|
| `sleeper.py` | All Sleeper API access, with a `.cache/` layer |
| `scoring.py` | League-specific scoring. **Not** Sleeper's `pts_ppr` |
| `priors.py` | Fits outcome distributions, correlations, play rates from 2023-25 |
| `injuries.py` | nflverse → ESPN → Sleeper designation resolver |
| `simulate.py` | Copula + lineup-state Monte Carlo, leverage, win-odds bands |
| `verdict.py` | One verdict per player — pros and cons cannot overlap |
| `draft.py` | Draft pick vs ADP and vs current positional rank |
| `analytics.py` | Power rating, optimal lineup, all-play |
| `facts.py` | Assembles the facts packet |
| `writeup.py` | Renders the documents; holds the four LLM system prompts |
| `validate.py` | The two confirmers, 23 deterministic checks |
| `snapshot.py` | Freezes inputs per week so `--replay` reproduces exactly |

## Do not undo these

Each of these looks like an oversight or a missed optimisation. Each was a
deliberate choice, made after measuring. The reasoning is in `HANDOFF.md`.

1. **`data/priors.json` is committed.** It is the fitted model, not a cache.
   Rebuilding depends on external endpoints that change. Do not gitignore it.
2. **Projections use `scoring.league_points()`, never `stats["pts_ppr"]`.** This
   league's defensive scoring differs by −2.3 points per team per week and
   reorders the DEF rankings.
3. **The boom/bust band is a fixed ±45% league-wide.** Tier-specific bands would
   pin "neither" near 50% for every player by construction and destroy the
   signal. A high "neither" share is meant to be informative.
4. **Ratings are points per week, not z-scores.** Z-scoring gave a 4.7 pts/week
   roster spread equal weight with an 11 pts/week simulation spread and produced
   tables where 12th projected higher than 8th.
5. **Availability is lineup-state enumeration, not a multiplier.** A multiplier
   scores an injured starter's slot as zero, which punishes teams with depth.
6. **`verdict.py` assigns at most one verdict per player.** Independent filters
   produce documents that praise a ceiling four lines above a bust warning.
   `validate.py` asserts disjointness; do not add a parallel classifier.
7. **`facts.py` drops `draft.commentary`'s sentiment buckets**, keeping only
   `cut` and `added`. Two classifiers disagreeing about one player is exactly
   how contradictions return. This is not dead code to tidy up.
8. **The workflow has no `schedule:` and no `on: push`.** Runs are manual. A
   stray run overwrites that week's snapshot with different data.
9. **Second-half injury exits stay in the priors.** Only players forced out
   before halftime are censored. A Q3 hamstring is a real fantasy outcome.
10. **The Gaussian copula stays** even though it moves team SD by only ~0.7
    points. It is correct, cheap, and would matter more in a stacked league.

## Conventions

- Comments explain **why**, not what. If a choice was measured, cite the number.
- No start/sit or roster advice in any output. The system prompts forbid it.
- New numbers go in the facts packet first, then get rendered.
- Any new document needs matching checks in `validate.py`.
- Run `python3 run.py` after any change and confirm both confirmers pass.

## Before committing

```bash
python3 run.py
```

Both documents must report `passed` in the validation summary. If either FAILS,
read `out/validation_wkNN.md` and fix the cause rather than loosening the check.

## Billing

Nothing in the pipeline costs money — Sleeper, nflverse and DynastyProcess are
free and unauthenticated, and the repo is public so Actions minutes are free.
The `--claude` flag calls the Anthropic API and is the only paid path; it is off
by default and absent from the workflow. If `ANTHROPIC_API_KEY` is exported in
your shell, Claude Code bills the API instead of the subscription — unset it.
