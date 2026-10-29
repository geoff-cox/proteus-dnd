# proteus-dnd — open TODOs cited from the code

The parked items of the private repository's `dev/open-todos.md` that concern
the code in this tree, copied as they stand there (a template of that
repository, `release/public/OPEN-TODOS.md`, refreshed by each export; the
private list is the record). **Item numbers are stable IDs.** Code comments
cite them — `services/dragndrop/export.py` → items 21, 22, 28; `policy.py` →
21; `cohort.py` → 23, 24; `qc.py` → item 3; `core/pretext.py` → 25 — and a
comment's `dev/open-todos.md item N` means item N here. Numbers are never
reassigned; the gaps are items that belong to parts of the private repository
this export does not carry (the short-answer service, the UI, the books).
Paths into the UI (`proteus_ui/`), the developer scripts (`scripts/`) and the
documentation (`docs/`, `dev/`) refer to the private repository.

Each item is tagged **[valuable]** (worth doing; approach known or partly
known) or **[decision-needed]** (parked until someone makes a design call).

---

2. **Group `<introduction>` context in single-question previews.** **[decision-needed]**
   *(Owner triage 2026-07-03: no design pick yet.)*
   A single-question preview captures one `<exercise>` unit; shared context in an enclosing
   `<exercises>`/`<exercisegroup>` `<introduction>` (e.g. AC §5.3) is omitted. Two options when
   revisited — *prepend the group intro to each member*, or *capture the whole division and
   drop sibling exercises*. No decision yet.

3. **Analytic idle-threshold default δ_max.** **[decision-needed]**
   *(F9 shipped this as configuration per rule 11 — this item tracks the open value question,
   not missing code.)* The journal never fixes δ_max; it only insists the value be *reported*
   (subsec-action-gaps-processing). F9 shipped one shared conservative default of **300 s**
   (`qc.DEFAULT_IDLE_THRESHOLD_S`), deliberately distinct from the 1800 s segmentation gap;
   reconstruct (`long_idle_gap`, the `active_work` segments) and analyze (`active_time_s`) take
   it as a keyword/config knob and record the value in force (F22 run configs;
   `dnd_attempt_metrics.idle_threshold_s`). Open when a real timing analysis lands: the right
   default for actual cohorts, whether the knob deserves a CLI surface, and whether
   `active_time` should offer the journal's truncation variant beside the implemented
   exclusion.
   *Measured 2026-08-15* — first real import (`real-data.tab`, 844 attempts, 9,961
   within-attempt inter-action gaps): median 2.9 s, p95 = 29 s, p99 = 112 s, p99.5 = 208 s,
   p99.9 = 605 s, max 1,434 s (the 1800 s segmentation gap caps the support by construction).
   **300 s sits above p99.5**: 31 gaps (0.3%) exceed it, touching 27/844 attempts — the
   default is mild, not wrong. A 120 s threshold would classify 92 gaps (0.9%) as idle. The
   distribution's body is seconds, not minutes, so anything ≥ 2 min is plausibly
   away-from-task; the value question is now a real trade-off between 120 s and 300 s rather
   than a guess. Still decision-needed — the pick should ride whatever timing analysis first
   consumes `active_time_s`.
   *Owner, 2026-08-15 (unblock questionnaire Q9):* the **300 s default is ratified as-is**;
   the real pick explicitly rides the first timing analysis, per the measurement.

21. **Per-era feedback-policy / check-cost record for the item covariates (F18).**
   **[decision-needed]** *(Rule-11 design pick made while implementing F18; conservative
   defaults in force.)* The F18 `boards` export carries the journal's `feedback_policy` and
   `check_cost` item covariates (tab-item-design-covariates), but the policy is a property of
   the **platform version at collection time**, not of the question source — the board cannot
   know it. Shipped as configuration: exports fill both from `policy.DEFAULT_EXPORT_POLICY`
   (`legacy` — every collected export predates the mid-2026 change) and
   `policy.DEFAULT_CHECK_COST` (`free`; the layered era's refusal gate would change it). The
   open design point the checklist amendment names: record the era **per data era** — a column
   on `raw_imports` set at ingest (`--feedback-policy` already tags reconstruction), or a dated
   platform-cutover config — and have exports resolve per question/attempt from that record.
   Until then the per-attempt FP-3 column (`dnd_attempts.feedback_policy`) is the honest
   granular source, and F20's modeling table should join it rather than the item-level default.
   See `policy.py` (the constants) and `export.py::_board_covariates`.

22. **"Common" wrong-state threshold in the item summary (F20).** **[decision-needed]**
   *(Rule-11 design pick made while implementing F20; conservative default in force.)* The
   item summary's dispersion family counts the "number of common wrong states"
   (tab-item-diagnostic-summary-fields) without fixing what "common" means. Shipped as
   configuration: a non-key **final scored state** counts when at least
   `export.DEFAULT_COMMON_STATE_MIN` (= 2, i.e. shared at all) attempts ended on it. Open for
   a real cohort: whether the threshold should scale with N (e.g. a fraction of attempts),
   and whether non-final wrong states belong in the count. See
   `export.py::_item_summary`.
   *Measured 2026-08-15* — first real import (`real-data.tab`, 214 attempts ending on a wrong
   final scored state): the count as shipped is dominated by an artifact. 15 states are
   shared by ≥ 2 attempts, but **the top repeat on every affected question is the all-zeros
   (empty-board) state** — 165 of the 169 shared-state attempt endings. Excluding it, the 49
   remaining wrong-final attempts land on 47 distinct states; only 2 states are shared, each
   by 2 attempts. So at N ≈ 15 attempts/question the metric carries essentially no
   misconception signal, and the open question that matters is **not** threshold scaling but
   whether the empty final state should count at all (it reads as abandonment — a check after
   a reset or an untouched board — not a wrong answer). Threshold scaling stays moot until a
   larger cohort.
   *Owner, 2026-08-15 (unblock questionnaire Q8) — **decided and implemented**:* the empty
   (all-tray) final state is **excluded** from `n_common_wrong_states` and surfaced
   separately as the additive `n_abandoned_final` column on the `item_summary` export
   (`export.py::_item_summary`; no migration — the export is read-time). What stays open
   here is only the original **threshold-scaling** question, moot until a larger cohort.

23. **Merge/split summaries in the error-profile figure (F13).** **[decision-needed]**
   *(Rule-11 design pick made while implementing F13; conservative default in force.)* The
   four `sec-merge-and-split-features` summaries (N_merge / E_merge / N_split / E_split) are
   slot/group counts — a third unit family beside the error-profile bars' card counts and
   pair counts. `subsec-error-profile-bars` admits grouping features to the display only "if
   … their units are labeled explicitly", and `fig-cohort-errors`' caption calls them
   "optional advanced bars". Shipped conservatively: the totals ride
   `CohortData.merge_split_totals` and surface **beside** the bars — a caption under the
   Explore error chart and a table in the dev text report — leaving the five-bar chart, and
   therefore the regenerated journal figure, untouched. The open call: whether they should
   graduate into the figure as explicitly unit-labeled advanced bars, which changes
   `dnd-cohort-errors.png` and so the book. See `cohort.py::_MERGE_SPLIT_ORDER` and the
   `errors` chart special-case in `Explore.py`.
   *Owner, 2026-08-15 (unblock questionnaire Q10):* **not yet — the caption surface is
   ratified.** Graduation is now trigger-gated, not decision-needed: pick it up when the
   first grouping analysis writes against `fig-cohort-errors`.

24. **Pair-index aggregation shape (F14).** **[decision-needed]**
   *(Rule-11 design pick made while implementing F14; conservative default in force.)* The
   journal makes Rand/ARI/VI cohort-scale-only and pooled from per-student configurations
   (`subsec-pairwise-metric-choice`) but never fixes the pooling formula. Shipped as the
   checklist's literal reading ("aggregate the per-check indices"): an **unweighted mean of
   per-check indices over all checks in scope**, the triple gated together on **≥ 2
   co-placed cards** with the one shared defining count n beside the means
   (`cohort.pair_index_means`; surfaced as a caption under the Explore grouping confusion
   chart — labeled all-check scope, since the chart beside it is unit-sample — and a
   dev-report table). The shared gate exists because VI alone is defined at a single
   co-placed card (trivially 0, the one-card partitions are identical) while Rand/ARI are
   None there; per-index skipping would bias VI toward agreement on checks the others
   exclude (PR #162 review). Open for a real grouping analysis: whether ARI should instead
   pool as a micro-average over summed per-check components (the mean of chance-corrected
   indices on tiny boards inherits the single-answer instability the journal warns about),
   whether the means should weight by each check's co-placed pair count, whether the sample
   should be the unit sample rather than all checks, and whether the display deserves a
   chart rather than a caption. See `cohort.py::pair_index_means` and the `confusion` chart
   special-case in `Explore.py`.
   *Owner, 2026-08-15 (unblock questionnaire Q11):* the **mean-of-per-check-indices shape is
   ratified** (with the shared ≥ 2 co-placed gate); the alternatives above wait for a real
   grouping analysis on a larger cohort.

25. **Offline, the pretext CLI's Runestone-services fallback crashes; a preview build
    needs a seeded `rs_services.xml`.** **[valuable]** *(Logged 2026-10-18 at the O-20
    re-pin's Checkpoint 1, PR #235; carried from item 15 (b), deleted when the pin
    reconciled — its (a), the `_static` string assertion, was already fixed by PR #178's
    URL-aware probe and passes on 2.55.0, and its (c), the `<directories>` resolution,
    holds: 2.55.0 resolves the values relative to `source/`, and `core/pretext.py`'s
    both-pairs fix builds.)* Re-verified on pretext 2.55.0: a portable `pretext build`
    fetches `https://cdn.jsdelivr.net/gh/pretextbook/html-static@2.55/dist/_static/runestone_services.xml`
    through `utils.rs_methods`, whose fallback reads `~/.ptx/2.55.0/rs_cache/rs_services.xml`,
    but the CLI's bundled `rs_cache.zip` extracts `runestone_services.xml` (and
    `dist-8.3.3.tgz`), so with the CDN unreachable the build dies with
    `fatal: cannot access local variable 'services_xml' where it is not associated with a
    value`. Every real-toolchain preview test fails the same way (`test_preview_e2e.py`; the
    builder branch of `test_default_builder_unavailable_without_toolchain` in
    `proteus_ui/tests/test_catalog_service.py`); CI never sees it (no `preview` extra).
    Recipe, unchanged since 2026-08-12: copy `runestone_services.xml` to `rs_services.xml`
    in the same directory. The 2026-10-18 cloud session, whose network policy denied
    `cdn.jsdelivr.net`, did so and all 48 tests in the four toolchain-sensitive files
    passed. Cited from `core/pretext.py`'s module docstring. The fix is upstream, in the CLI.

26. **The PV-3 fixture and the records around it describe 2.42's page; the real 2.55 page
    differs in two places the prune deliberately leaves alone.** **[decision-needed]**
    *(Logged 2026-10-18, the O-20 re-pin's §3.3 drift report, PR #235.)* Every prune
    selector still matches and fires on real `html-static@2.55` output (both fixture-book
    cardsorts), so nothing in `preview_prune.py` moved. But (i) `#ptx-navbar
    .nav-other-controls`, kept "for the dark-mode button", now holds a Read-aloud button, a
    hidden player and a settings dialog, and `#light-dark-button` is absent from the raw
    page; (ii) the cardsort answer key no longer ships as a sibling
    `script[data-component="dragndrop-data"]` with a `match` array — it rides as `for` and
    `data-category` attributes on `li[data-subcomponent="dropzone"]`, and the `li.draggable`
    / `li.dropzone` classes are gone (only the `data-subcomponent` attributes remain). The
    A20/BUG-20 policy is unchanged in substance (the key is still in the page; DnD previews
    stay analyst-facing), but `src/tests/fixtures/preview/built_cardsort.html`, the
    `preview_prune.py` docstring, `TestAnswerKeyPolicy` and `TestSurvival` in
    `test_preview_prune.py`, the `scripts/preview/build_preview_debug.py` survival list and
    `important-references.md` §3.2's A20 bullet all describe the old shape. Owner decides
    whether the fixture and the records are refreshed to 2.55's page, and whether the
    read-aloud widget now kept in the navbar should be pruned too; a PV-3 change, not a
    packaging one (ruling R3 of the re-pin brief).

27. **A full suite run with a real pretext CLI rewrites two tracked demo pages.**
    **[valuable]** *(Logged 2026-10-18, PR #235.)* Some proteus_ui DnD tests
    (`test_dnd_questions_page.py`, `test_dnd_questions_filter.py`,
    `test_dnd_legend_service.py`, `test_dnd_boards_service.py` or the `render_legend` call
    in `test_dnd_display_only_guard.py`) render `demo__shapes` without a `cache_root`, so
    the default root — the relative `data/question_previews` — is written under the
    checkout, and the committed `data/question_previews/demo/demo__shapes.html` and
    `demo__shapes.legend.html` (built at `html-static@2.50`, commit `7c1c31a`) come back as
    2.55 output. CI never sees it (no toolchain); the 2026-10-18 session restored the two
    files. Fix candidates: give those tests a `tmp_path` cache root, or stop tracking the
    demo pages; either is a small change outside the re-pin PR.

28. **The attempt-shaped exports' `import_sha256` assumes one import per attempt.**
    **[decision-needed]** (only if a corpus departs from one export per offering)
    `export._ATTEMPT_IMPORT_JOIN` (PR #236,
    the two Copilot rounds of 2026-10-19) reads an attempt's offering key off the attempt's
    window — the events of its student on its logged identifier, in its session, between
    `started_at` and `ended_at` — with `MAX(raw_imports.file_hash)` as the deterministic pick.
    That every attempt's events share one import is a property of ingest and of the frozen
    corpus, not of the join: a row two exports share collides on `event_id` and is skipped
    (attributed to the first import, `core/ingest/events.py`), and the frozen run ingests one
    export per offering with no student id recurring across terms (procedure Stage 1.1, 2.2).
    Two shapes would break it silently: one offering ingested as date-split or overlapping
    files (a session straddling the cut gives one attempt two imports and MAX an arbitrary
    pick), or a non-default `SegmentationConfig(split_on_session_change=False)` whose attempt
    spans sessions drawn from two imports (the window then yields only the first session's
    import). Neither is reachable in the frozen run. If a future corpus departs from one
    export per offering, the escalation is to persist the import on the attempt — a
    `dnd_attempts.import_id` column written by `reconstruct` (which already reads
    `e.import_id` in `_timeline`) behind a migration and a reconstruct `stage_version` bump —
    or, as a tripwire first, `COUNT(DISTINCT e.import_id)` in the rollup with a warning when it
    exceeds 1.

