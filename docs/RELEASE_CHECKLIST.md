# Release Checklist

Run `make release` to automate most steps. This checklist covers what to verify manually before shipping.

---

## RC1 Status (0.1.0-rc1 — 2026-05-13)

| Item | Result |
|------|--------|
| Tests (SQLite) | 607 pass, 2 skip |
| Evals | 66/66 |
| Release gate | PASS |
| Security audit | PASS (4/4 checks, 1 WARN — insecure dev defaults, expected) |
| Docker smoke | PASS (8/8 functional; compose rebuild WARN — disk constraints) |
| Backup/restore | PASS (8/8 checks, migration upgrade clean) |
| Version | 0.1.0-rc1 |

## Pre-Release

- [ ] All 607+ tests pass (SQLite): `make test`
- [ ] Eval harness passes 66/66: `make evals`
- [ ] Release gate passes: `make gate`
- [ ] Security scan passes: `make security`
- [ ] Migrations apply cleanly on SQLite: `alembic upgrade head`
- [ ] Migrations apply cleanly on Postgres (if Postgres CI available)
- [ ] No leakage metrics > 0 (checked by release gate)
- [ ] Web UI builds: `cd web && npm run build`
- [ ] Docker image builds and passes health check: `docker build -t mimir:test . && docker run --rm mimir:test curl -s http://localhost:8787/health`
- [ ] Docker smoke test: `bash scripts/docker_smoke_test.sh` (requires 20GB+ free disk)
- [ ] Migration head revision matches `tests/test_migrations.py` hardcoded revision (`0012`)
- [ ] Backup created and verified: `python -m mimir.backup.create && python -m mimir.backup.verify <path>`

## Security

- [ ] Security scan passes: `make security` (0 FAIL, no runtime vulns)
- [ ] No secrets in logs (check `mimir.logging` output)
- [ ] API keys not returned after creation (only SHA-256 hash stored)
- [ ] Quarantined memories not reactivatable via content update (fixed P18)
- [ ] System mutation endpoints disabled in prod (`MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=false`)
- [ ] Slack signing secret verified on all callbacks
- [ ] Production CORS origins set (no `*`)
- [ ] `MIMIR_SECRET_KEY` changed from default
- [ ] `ACCESS_CONTROL_MATRIX.md` reviewed: `docs/ACCESS_CONTROL_MATRIX.md`
- [ ] Tailscale forbidden command scan passes (part of `make security`)

## Backup

- [ ] Pre-release backup created: `python -m mimir.backup.create --out backups/rc1`
- [ ] Backup verified: `python -m mimir.backup.verify backups/rc1/<archive.zip>`
- [ ] Restore smoke-tested: `python -m mimir.backup.restore <archive.zip> --dry-run`

## Deployment

- [ ] Version set to `0.1.0-rc1` in `pyproject.toml`, `mimir/__version__.py`, `web/package.json`
- [ ] `GET /health` returns `version: 0.1.0-rc1`
- [ ] Migration revision matches hardcoded head in `tests/test_migrations.py`
- [ ] `MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS` is `false` (or unset) in prod

## Post-Release Verification

- [ ] `GET /health` returns `{"status": "ok", "version": "0.1.0-rc1"}`
- [ ] `GET /api/system/readiness` returns `{"ready": true, ...}`
- [ ] Worker starts without errors
- [ ] Recall returns results
- [ ] Approval workflow functions

---

## Automated Gate

```bash
make release
```

Runs in order:
1. `pytest tests/ -q`
2. `alembic upgrade head`
3. `python -m evals.runner --suite all`
4. `python -m evals.release_gate`
5. Web build
6. Wheel build
7. Generates `reports/release/latest.md` and `latest.json`
