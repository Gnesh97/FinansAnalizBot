## EXECUTIVE SUMMARY
Verdict:        Investigated all project-level markdown docs (Explore agent, evidence-based
                 against current code/config) and acted on findings. GREEN (docs-only change,
                 no code touched).
Changed:        Deleted 5 stale/redundant docs + 1 orphaned 22MB data directory; updated 5
                 docs with current facts (746 tests, 177-symbol universe, Tiingo live
                 profile, DSS nightly chain, runner.py split, no Docker scripts).
Suite:          Not re-run -- no Python file changed this phase.
Anomalies:      None.
Open items:     None from this phase.
Operator action: None required.

---

## 1. Context

Following the prior "what's next" recommendation, the operator asked to find and delete
unnecessary markdown docs and update necessary-but-stale ones. Investigated via an Explore
agent, explicitly scoped to the project's own docs (excluding `.agent/`, `.agents/`, `ECC/`,
`.pytest_cache/` -- Claude Code plugin/tooling infrastructure, not project documentation) and
briefed on everything that changed this session (runner.py's 5-way split, bot.py's 81% cut,
the 6-file dead-code deletion, new risk_engine.py/strategy_dashboard.py, the Tiingo-live-profile
architecture) so staleness could be judged accurately against ground truth, not assumption.

## 2. Deleted (6 items, evidence per file)

- `docs/PROJECT_STATUS.md` -- 6+ weeks stale, describes a retired intraday-centric workflow
  CLAUDE.md now states is "retired", fully redundant with (and predates) the other status docs.
- `docs/PROJECT_EVOLUTION_AND_CURRENT_STATUS.md` (857 lines) -- self-labeled a point-in-time
  snapshot requiring re-update on major changes; never updated since 2026-06-21 despite the
  Tiingo migration, intraday retirement, and this session's refactor. Zero inbound links.
- `docs/PROJECT_STATUS_CURRENT.md` -- documented as stale *twice already* in the project's own
  audit history (`repo_status_audit_20260715.md`, then "fixed" in `phase3a_cutover_20260718.md`,
  then stale again within a week on the same claim: active data folder pointed at the retired
  `rs_v2_sip` instead of live `rs_v2_tiingo`). Evidence this doc's maintenance model doesn't
  hold up in practice.
- `PROJE_SURECI.md` (278 lines, Turkish) -- a mid-refactor design journal from 2026-06-20/21;
  describes strategy module layout and a validation-experiment set that have since been fully
  superseded by later work. No inbound links.
- `docs/testing/rs_v2_provider_contract_forensics.tdd.md` -- one-off TDD evidence log for a
  single completed task (2026-07-16); this project's actual convention is
  `reports/audit/<phase>_<topic>_<date>.md`, not `docs/testing/`. Sole file in that directory,
  now removed along with the empty directory.
- `testdata/delisthisseler/` (README + 20 CSVs, ~22MB) -- a third-party "Arandkei" delisted-
  securities dataset with zero code references anywhere in the current tree or in the (now also
  deleted) delisted-data research modules' history -- confirmed orphaned both before and after
  this session's earlier dead-code cleanup, predating and unrelated to that deleted island.

## 3. Updated (5 items)

- **`PROJECT_STATUS.md`** (root -- the only status doc README actually links to): fully
  rewritten as a short pointer to CLAUDE.md plus current headline facts -- 746 tests (was:
  "275 passing"), 177-symbol frozen hashed universe (was: "~200 active stocks"), Tiingo as the
  live profile with rs_v2_sip/clean noted as frozen history (was: unmentioned), the DSS
  nightly chain (was: unmentioned), and an accurate, evidence-linked survivorship-bias section
  (PARA's documented EARLY_TERMINAL exception, the abandoned Alpaca-delisted-data effort).
- **`README.md`**: added a "Nightly DSS Chain" section identifying the systemd timer as the
  *primary* production path (previously undocumented anywhere -- confirmed via grep during the
  earlier bot.py dead-code phase) and reframing the BAT-file workflow as secondary/manual.
- **`docs/COMMANDS.md`**: removed a "Docker ile tasima" section referencing
  `scripts/export_docker_image.bat`/`load_docker_image.bat` -- confirmed via `git log --all`
  these files never existed in this repo's history (aspirational content, never implemented);
  added a note that most documented commands default to the frozen `rs_v2_sip` profile unless
  `--data-profile rs_v2_tiingo` is passed explicitly.
- **`docs/ARCHITECTURE_REVIEW.md`**: added a dated note at the top flagging that `runner.py`
  has since been split into 5 modules (the doc's module map/refactor-sequence body describes
  the pre-split state and is left as historical context rather than rewritten in full, given
  the doc's own "Safe Refactor Opportunities" proposals for still-unbuilt modules
  (`paths.py`, `paper_state.py`, etc.) remain valid and worth keeping); noted its own
  `report_schemas.py` suggestion is already implemented.
- **`research_sandbox/README.md`**: added `rs_factor_combination.py` (added earlier today) to
  the Contents list, which only listed `rs_rotation_sensitivity.py`.

## 4. Left unchanged (confirmed current)

- `AGENTS.md` -- every validation command cross-checked against `universe_scanner.py`'s live
  argparse flags, all present; no references to deleted bot.py modes.
- `docs/CODEX_TASK_TEMPLATE.md` -- generic reusable template, no technical claims to go stale.
- `quantconnect/rs_m126_top30_sma100_no_regime/README.md` -- every specific claim
  (delisting/price-normalization/split-dividend/PIT blocker statuses) cross-checked against
  `testdata/reports/latest/quantconnect_port_contract.json` and `main.py`'s actual
  `RuntimeError` guards; matches exactly.
- `CLAUDE.md` -- explicitly out of scope (actively maintained standing-rules file, not part of
  this cleanup).

## 5. Verification

No Python file was touched this phase -- markdown-only change, so the full test suite was not
re-run (nothing in it depends on doc content). Confirmed via `git status --porcelain` that only
the intended 11 markdown paths (6 deleted, 5 modified) plus the orphaned data directory changed.
Confirmed `testdata/delisthisseler/`'s 20 CSVs were never git-tracked (only its README was),
consistent with the "orphaned local data, not even committed" finding.

## 6. Git state

```
$ git status --porcelain (before this phase's commit)
 M PROJECT_STATUS.md
 D PROJE_SURECI.md
 M README.md
 M docs/ARCHITECTURE_REVIEW.md
 M docs/COMMANDS.md
 D docs/PROJECT_EVOLUTION_AND_CURRENT_STATUS.md
 D docs/PROJECT_STATUS.md
 D docs/PROJECT_STATUS_CURRENT.md
 D docs/testing/rs_v2_provider_contract_forensics.tdd.md
 M research_sandbox/README.md
 D testdata/delisthisseler/README.md
```

## 7. Open items

None specific to this phase. Standing backlog (runner.py Clusters 3/5, v2 migration cluster,
14 undocumented CLI flags, Monday's first post-lid-switch-fix nightly run) remains as tracked
in earlier phase reports.
