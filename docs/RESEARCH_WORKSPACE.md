# Research workspace

## Scope

The investor application owns accounts, execution, scheduling and active plans.
The research workspace owns algorithm comparisons, reproducible parameter searches
and research records. It contains no duplicate trading/account controls.

## Workflow

1. Copy a saved investment plan as the research baseline without activating it.
2. Compare named regressors, trend and buy-and-hold under that baseline's identical
   portfolio, financing and transaction-cost rules, using one frozen daily dataset.
3. Tune one regressor around explicit parameters. The current plan and recipe
   defaults remain references. Select parameters using the search years only;
   check the chosen candidate and original plan on later years after selection.
4. Keep the result, data identity, parameter differences, annual returns and model
   validation/fallback receipts. Interrupted runs resume completed stages.
5. Send a completed candidate to an investment-plan draft. Only the investor
   application's explicit save activates it. Existing profile plans stay intact.

Daily research does not silently reinterpret an intraday plan. Intraday baseline
plans must be changed explicitly in the investor application before this workflow.
Legacy experiments remain readable in research records but are not comparable to
the new engine and cannot be promoted through the new workflow.

## Interpretation

Search score = annualized net return - 0.5 * absolute maximum drawdown
- 0.25 * standard deviation of annual returns. It is a transparent research
ranking, not a profit guarantee. A later historical check is not truly unseen
data if the user has previously researched that period. Repeated searches can
still overfit. No AI calls are needed for comparisons or tuning.

## Backups

Pre-redesign source snapshot: `963b26a`, branch
`backup/pre-research-workspace-20260930` on `Yummn/ai-stock-bot`.
Runtime data and secrets are local only. Embedded broker simulation account
identifiers are redacted in Git snapshots; local configuration is unchanged.

## Verification (2026-10-01)

- 384 tests passed in the full model runtime (`pytest --assert=plain tests -q`).
  Standard assertion rewriting hit a Windows native access violation during
  collection; the complete plain-assertion run retains the same assertions.
  This is not proof the previously observed host/runtime instability is fixed.
- Frontend type check and investor build passed. Browser checks cover 360, 390,
  1440 and 1920 px, nonblank chart pixels, per-model draft persistence, old-hash
  routing, request parameters, explicit draft handoff/save, account and other
  profile preservation. Screenshots were visually inspected.
  Node also encountered a native V8 build failure; the final verified build
  used Node with `--max-old-space-size=1024 --no-maglev`. Logs retain the failed
  attempts as well as the successful build. No machine-wide settings changed.
- Real frozen market data: compare HGB / Ridge / trend / holding over 2023-2024;
  tune Ridge regularization around 30, then check the search winner over 2025.
  The original HGB plan stays a reference; model-specific search cannot be won
  by a different model. Ridge's default regularization 100 beat the tested
  9 / 30 / 90 alternatives in search. This validates the workflow, not a claim
  of improved future returns or an exhaustive search across every model.
- 2023-2024 annualized returns: HGB 15.46%, Ridge 13.99%, trend 14.29%, holding
  20.83%. 2025 independently started check: Ridge +27.26%, HGB +26.92%, holding
  +21.04%; drawdowns respectively -6.34%, -3.56%, -7.56%. Holding is reported
  even when it beats the active strategies. These use the balanced plan's
  rules and the frozen local dataset, not current/future performance estimates.
- Local receipts, logs, screenshots and isolated test database are under
  `docs/reports/research-workspace-20261001/` and excluded from Git. One earlier
  local verification worker exited unexpectedly; the saved task was resumed.
  No production account was reset and no broker endpoint was called.
- Deployed to the existing 8772 service. Production revision 81, active aggressive
  slot and plan identity `a96347e92343b00dfa83ac0b` are unchanged. Account, curve,
  pending decision and saved profile strategies matched before/after; only the
  additive null `model_tuning` schema field is excluded from that comparison.
