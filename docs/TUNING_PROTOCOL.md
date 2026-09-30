# Parameter research protocol

The `scripts/tune_stocklab.py` runner performs a finite, resumable search through
the local StockLab API. It does not place orders or use a large-model service.

## Comparisons

- Keep the current settings as a named backup and `before.json`.
- Manually reduce neural-network size, use regularization, and evaluate a
  three-year pooled-data configuration. This is a hypothesis, not a guaranteed
  improvement over the existing single-symbol or longer-window configuration.
- For each of the five models and three presets, compare the old parameters with
  four candidates: manual reference, coarse search, fine search, random mutation.
- Use 2021, 2022, 2023 for candidate ranking. Retain old parameters when the score
  increase is less than 0.002. Score includes annual return, excess over holding,
  and maximum drawdown, with existing preset-specific weights.
- Freeze each winning preset, then compare old and selected parameters on 2024
  and 2025. These later results do not participate in candidate ranking, but do
  participate in the expert deployment review.
- Confirm both configurations use identical annual prices, dates and bar counts.
  Require full trading-universe coverage and zero local-strategy fallback.
- Save the two reviewed improvements as daily-use presets: RandomForest
  conservative and HistGradientBoosting balanced. Keep the other thirteen
  presets unchanged. Store all search winners in a separate experimental plan.
- Leave custom presets, account risk, local rules, AI prompts, symbols and manual
  labels unchanged.

The daily-use default is HistGradientBoosting balanced, chosen from search-period
tree-model scores and its low CPU cost before reviewing later-year outcomes.

## Caveats

The later years appeared in earlier project research, so they are not pristine
unseen data. No result proves future profitability. A current ETF universe also
has survivorship and selection bias. This is annual daily-bar simulation, with
next-open fills, fees and slippage; dividends are not reinvested. Each annual
experiment starts with the same balance rather than compounding between years.
The native annual scene is a majority vote over the trading universe, not an
independent market index. A preset may have no matching specialty year.
Every within-model comparison used matching price fingerprints. Historical
provider refreshes changed the fingerprints between tree and neural model
groups, so this research does not rank all five models directly. The deployment
review is a practical release decision made after seeing the later-year results,
not a pre-registered statistical test. Most rejected candidates lost stronger
market participation or became inactive despite improved search-period scores.

Prediction caches record each training-data/configuration fingerprint. During a
single annual experiment, pooled training samples and identical fitted models
are reused across symbols. Single-symbol models are not retained unnecessarily.
This changes computation cost, not prediction values. Neural networks retain
chronological validation, embargo, early stopping and CUDA when available.

Generated evidence is stored under `data/v2/research/2026-09-27`, including
`state.json`, `REPORT.md`, `before.json`, `manual.json` and `ready.json`.
Each API job and all learning candidates are also retained in SQLite.

## Reproduction

From the repository directory, with StockLab running on port 8765:

```powershell
.\.venv\Scripts\python.exe scripts/tune_stocklab.py --phase all
```

The runner resumes previously completed work from `state.json`. Use a different
`--output` directory for a new independent experiment. Never reuse a later-year
audit result to choose another candidate while calling it an untouched holdout.
