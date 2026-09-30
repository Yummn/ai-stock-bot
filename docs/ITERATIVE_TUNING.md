# Five-model, four-preset parameter research

This follow-up replaces the previous one-round screen with independent research
for RandomForest, HistGradientBoosting, XGBoost, PyTorch MLP and 1D-CNN, including
aggressive, balanced, conservative and custom presets.

## Reproducible inputs

The runner takes a versioned settings snapshot and freezes all cached daily OHLC,
adjusted closes and volume through 2025 into a separate SQLite database. It never
downloads or silently substitutes market history during a comparison. The
manifest records provider identities, actual available dates and hashes; newer
ETFs do not acquire fictitious pre-IPO history. Every experiment uses the same
ten trading ETFs, supplementary training universe, costs, risk limits and seed.

All model training stops before the first trading day of each test year. The
label's future horizon must also finish before that cutoff. Neural validation
is temporal with a horizon embargo and training-only normalization. Signals
are executed at the next trading day's opening price, not the signal close.

## Feedback Waves

1. Hand-authored alternatives change model complexity, training window, pooled
   versus scenario training, next-day versus five-day targets and binary versus
   five-class labels. Each model/preset also retains its own original reference.
2. Increase and decrease capacity around that preset's selected center. Changes
   include tree count/depth/leaf regularization or neural width, learning rate,
   weight decay and convolution window. Do not assume all models prefer the same
   regularization or training horizon.
3. Test both directions for buy thresholds and sell thresholds. An additional
   search-only probability-distribution adjustment is included for near-inactive
   candidates. It does not force a minimum number of trades in a weak market.
4. If CNN balanced/custom still selects a near-inactive reference, additionally
   compare shorter 3/5-bar sequences with 20-bar sequences and joint entry/exit
   thresholds. This is conditional diagnostic exploration using only search
   years, with the same objective, not a change of scoring to favor new settings.

Search years are 2021, 2022, 2023 and 2024. 2025 is evaluated only after these
waves. Candidate rankings never use the 2025 result; deployment rejects a
material later-year regression rather than adopting a changed parameter just
to claim that every preset changed. Custom is scored across all search years;
it is not overwritten by the balanced preset.

The runner is bounded to three main waves plus conditional CNN feedback, not an indefinite claim
to find a global optimum. The web application's independent continuous learning
feature remains available for subsequent experiments.

## Explicit Selection Preferences

Each annual value is return + 0.35 * excess return + drawdown weight * negative
maximum drawdown. Drawdown weights are 0.35 aggressive, 0.7 balanced, 1.2
conservative and 0.7 custom. Three named presets combine target-scene mean,
other-scene mean and overall mean with weights 0.5 / 0.2 / 0.3. Custom uses the
overall mean. Add 0.15 * worst annual return to penalize unstable candidates.

For scoring only, equal-money holding benchmark returns of at least 10% label
a strong year, at most -5% a weak year, and the remainder a sideways year.
This avoids majority voting across heterogeneous ETFs mislabeling portfolio
performance. It does not change live regime selection or training labels.
An aggressive preset's strong-year value has up to a 0.03 deduction for average
exposure below 15%, making a completely inactive model an unattractive bull
strategy. These weights and exposure preferences are judgment calls, not facts
about what is guaranteed to profit.

Search improvement must exceed 0.002. A later-year score regression exceeding
0.05 normally rejects deployment. Defensive balanced/conservative presets can
instead be accepted when the later year's return remains nonnegative and maximum
drawdown is at least halved, with at least a 0.5 percentage-point absolute decrease.
This is an expert deployment tradeoff added after inspecting the later-year
results, not a preregistered holdout claim; it does not re-rank candidates or use
2025 to optimize parameters. The lower bull return is explicitly disclosed.
Rejected candidates stay inspectable; original presets
remain valid references. Preserve account, local, AI and data settings.

## Run And Review

Run from the repository root with the virtual environment and web service:

```powershell
.venv\Scripts\python.exe scripts/iterate_stocklab.py --phase prepare
.venv\Scripts\python.exe scripts/iterate_stocklab.py --phase manual --isolated
.venv\Scripts\python.exe scripts/iterate_stocklab.py --phase refine --isolated
.venv\Scripts\python.exe scripts/iterate_stocklab.py --phase feedback --models cnn --isolated
.venv\Scripts\python.exe scripts/iterate_stocklab.py --phase audit --isolated
.venv\Scripts\python.exe scripts/verify_iterative.py
.venv\Scripts\python.exe scripts/iterate_stocklab.py --phase publish
```

Inspect results between waves. Only publish after verification passes. Completed
evaluations and annual simulations are checkpointed, so a failed process can
be restarted without losing completed results. There is no annual computation
timeout. Do not run two research writers simultaneously; CPU/GPU work remains
sequential to protect a 16 GB machine and keep comparisons deterministic.

Artifacts are under `data/v2/research/iterative-2026-09-27`: `before.json`,
`manifest.json`, `state.json`, `REPORT.md`, `verification.json`, `ready.json`,
and gzip-compressed full annual results under `runs/`. Published suggestions
and all evaluated candidates are saved in the main database for the web UI.
Publication uses optimistic settings revisions and refuses to overwrite a
concurrent user's changes. A named backup plan permits rollback.

## Limits

2021-2025 were previously examined in this project. The later-year check is not
a pristine untouched holdout. Repeated searches can overfit validation years
even when model training itself has no future leakage. Results use one seed
and the current ETF pool, with survivorship bias, not all historical securities.
Raw-price execution and adjusted-price features do not simulate dividend
reinvestment. Quotes can be revised upstream after the frozen snapshot.
Classifier scores are not calibrated probabilities of profitable trades.
Historical improvements cannot guarantee future returns or positive weak-market
profits. No live orders or external large-model calls occur in this protocol.
