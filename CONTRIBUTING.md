# Contributing

## Scope

Welcome:

- Protocol / UI breakage fixes with repro steps (no real tokens)
- Docs, translation, clearer error messages
- Tests that do not need live credentials

Not welcome:

- PRs whose purpose is unauthenticated abuse or ToS evasion for profit
- Commits that include secrets, accounts, or private hostnames

## Dev flow

```bash
python -m venv .venv
# activate
pip install -r requirements.txt
cp config.example.json config.json
```

From private monorepo maintainers only:

```bash
python scripts/export_to_regkit.py
python scripts/export_to_regkit.py --check-only
```

## PR checklist

- [ ] No secrets in diff
- [ ] Docs updated if behavior/config changed
- [ ] Prefer small, reviewable commits
