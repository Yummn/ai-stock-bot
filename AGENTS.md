# Project working agreements

- Before each major change, create and push a source backup to a dated `backup/` branch on the existing GitHub origin. Confirm the push succeeded before implementation. Do not force-push or overwrite unrelated remote changes.
- Audit staged files before every push. Never upload credentials, account identifiers, access-key files, databases, private broker configuration, local reports, environments or native SDKs. Four existing EMT helper files contain local account identifiers: preserve local behavior and redact identifiers in the Git snapshot only. Do not stage these files without this audit.
- Keep paper/live account operations outside the research workspace. Research must not change the active investment plan or place orders.
- Research comparisons must share a frozen market snapshot, portfolio execution rules and costs. Parameter selection and later historical checks must be separated. Failed or incomplete candidates must not be promoted as winners.
- Preserve per-profile settings, existing accounts and historical results. Test with isolated stores; do not reset the user's account for verification.
