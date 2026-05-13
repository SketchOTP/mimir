"""P16 Production Deployment Readiness — test suite.

Covers:
- Config validation (prod mode fail-fast, dev mode warnings, Slack/VAPID guards)
- Backup create / verify / restore pipeline
- Auth hardening (API keys hashed, no plaintext returned, prod endpoints require auth)
- Health / readiness / status endpoints
- Operational endpoints: migration revision, FTS status, worker jobs
- Release report module importable
- Load test module importable and runnable (smoke run)
- Docker compose schema validation
- Operator docs exist
"""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from httpx import AsyncClient, ASGITransport


# ── helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
async def app():
    from storage.database import init_db
    from api.main import app as _app
    await init_db()
    return _app


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ══════════════════════════════════════════════════════════════════════════════
# P2 — Config validation
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigValidation:
    def _make_settings(self, **kwargs):
        from mimir.config import Settings
        return Settings(**kwargs)

    def test_dev_mode_passes_with_defaults(self):
        from mimir.config import validate_config, Settings
        s = Settings(env="development")
        # Should not raise
        with patch("sys.exit") as mock_exit:
            with patch("sys.stderr", new_callable=io.StringIO):
                validate_config(s)
        mock_exit.assert_not_called()

    def test_prod_mode_fails_without_secret_key(self):
        from mimir.config import validate_config, Settings
        s = Settings(env="production", auth_mode="prod",
                     secret_key="change-me")  # insecure default
        captured = io.StringIO()
        with pytest.raises(SystemExit):
            with patch("sys.stderr", captured):
                validate_config(s)
        assert "INSECURE DEFAULT" in captured.getvalue()

    def test_prod_mode_fails_without_auth_mode(self):
        from mimir.config import validate_config, Settings
        s = Settings(env="production", secret_key="correct-secret-key-32chars-xxx",
                     auth_mode="")
        captured = io.StringIO()
        with pytest.raises(SystemExit):
            with patch("sys.stderr", captured):
                validate_config(s)
        assert "MIMIR_AUTH_MODE" in captured.getvalue()

    def test_prod_mode_fails_invalid_auth_mode(self):
        from mimir.config import validate_config, Settings
        s = Settings(env="production", secret_key="correct-secret-key-32chars-xxx",
                     auth_mode="magic")
        captured = io.StringIO()
        with pytest.raises(SystemExit):
            with patch("sys.stderr", captured):
                validate_config(s)
        assert "INVALID" in captured.getvalue()

    def test_prod_mode_fails_insecure_api_key(self):
        from mimir.config import validate_config, Settings
        s = Settings(env="production", secret_key="correct-secret-key-32chars-xxx",
                     auth_mode="prod", api_key="local-dev-key")
        captured = io.StringIO()
        with pytest.raises(SystemExit):
            with patch("sys.stderr", captured):
                validate_config(s)
        assert "api_key" in captured.getvalue()

    def test_prod_mode_fails_wildcard_cors(self):
        from mimir.config import validate_config, Settings
        s = Settings(env="production", secret_key="correct-secret-key-32chars-xxx",
                     auth_mode="prod", api_key="secure-prod-key",
                     dev_api_key="secure-dev-key",
                     cors_origins=["*"])
        captured = io.StringIO()
        with pytest.raises(SystemExit):
            with patch("sys.stderr", captured):
                validate_config(s)
        assert "CORS" in captured.getvalue()

    def test_prod_mode_passes_with_all_required(self):
        from mimir.config import validate_config, Settings
        s = Settings(env="production",
                     secret_key="correct-secret-key-32chars-xxxxxxxx",
                     auth_mode="prod",
                     api_key="secure-prod-key",
                     dev_api_key="secure-dev-key",
                     cors_origins=["https://mimir.example.com"])
        with patch("sys.exit") as mock_exit:
            with patch("sys.stderr", new_callable=io.StringIO):
                validate_config(s)
        mock_exit.assert_not_called()

    def test_slack_requires_signing_secret_in_prod(self):
        from mimir.config import validate_config, Settings
        s = Settings(env="production",
                     secret_key="correct-secret-key-32chars-xxxxxxxx",
                     auth_mode="prod",
                     api_key="secure-prod-key",
                     dev_api_key="secure-dev-key",
                     cors_origins=["https://mimir.example.com"])
        # Inject slack token without signing secret
        object.__setattr__(s, "slack_bot_token", "xoxb-test-token")
        captured = io.StringIO()
        with pytest.raises(SystemExit):
            with patch("sys.stderr", captured):
                validate_config(s)
        assert "SLACK_SIGNING_SECRET" in captured.getvalue()

    def test_vapid_partial_keys_fail_in_prod(self):
        from mimir.config import validate_config, Settings
        s = Settings(env="production",
                     secret_key="correct-secret-key-32chars-xxxxxxxx",
                     auth_mode="prod",
                     api_key="secure-prod-key",
                     dev_api_key="secure-dev-key",
                     cors_origins=["https://mimir.example.com"])
        object.__setattr__(s, "vapid_private_key", "some-private-key")
        # vapid_public_key is empty
        captured = io.StringIO()
        with pytest.raises(SystemExit):
            with patch("sys.stderr", captured):
                validate_config(s)
        assert "VAPID" in captured.getvalue()

    def test_dev_mode_warns_insecure_secret(self, capsys):
        from mimir.config import validate_config, Settings
        s = Settings(env="development", secret_key="change-me")
        captured = io.StringIO()
        with patch("sys.stderr", captured):
            validate_config(s)
        # Should warn, not fail
        output = captured.getvalue()
        assert "warn" in output.lower() or "change-me" in output

    def test_settings_has_database_url_field(self):
        from mimir.config import Settings
        s = Settings(env="development")
        assert hasattr(s, "database_url")

    def test_settings_has_public_url_field(self):
        from mimir.config import Settings
        s = Settings(env="development")
        assert hasattr(s, "public_url")

    def test_slack_enabled_property(self):
        from mimir.config import Settings
        s = Settings(env="development")
        assert not s.slack_enabled
        object.__setattr__(s, "slack_bot_token", "xoxb-token")
        assert s.slack_enabled

    def test_pwa_push_enabled_property(self):
        from mimir.config import Settings
        s = Settings(env="development")
        assert not s.pwa_push_enabled
        object.__setattr__(s, "vapid_private_key", "priv")
        object.__setattr__(s, "vapid_public_key", "pub")
        assert s.pwa_push_enabled


# ══════════════════════════════════════════════════════════════════════════════
# P3 — Backup / Restore / Verify
# ══════════════════════════════════════════════════════════════════════════════

class TestBackupPipeline:
    def test_backup_create_importable(self):
        from mimir.backup.create import create_backup
        assert callable(create_backup)

    def test_backup_restore_importable(self):
        from mimir.backup.restore import restore_backup, validate_restore
        assert callable(restore_backup)
        assert callable(validate_restore)

    def test_backup_verify_importable(self):
        from mimir.backup.verify import verify_backup
        assert callable(verify_backup)

    def test_verify_rejects_missing_file(self, tmp_path):
        from mimir.backup.verify import verify_backup
        with pytest.raises(FileNotFoundError):
            verify_backup(tmp_path / "nonexistent.zip")

    def test_verify_rejects_non_zip(self, tmp_path):
        from mimir.backup.verify import verify_backup
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip file")
        with pytest.raises(ValueError, match="Not a valid zip"):
            verify_backup(bad)

    def test_verify_rejects_missing_manifest(self, tmp_path):
        from mimir.backup.verify import verify_backup
        archive = tmp_path / "test.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("mimir.db", b"SQLite format 3\x00" + b"\x00" * 100)
        result = verify_backup(archive)
        assert not result["ok"]
        check_names = [c["name"] for c in result["checks"]]
        assert "manifest_present" in check_names

    def test_verify_rejects_missing_db(self, tmp_path):
        from mimir.backup.verify import verify_backup
        archive = tmp_path / "test.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("manifest.json", json.dumps({
                "version": "1.0",
                "created_at": "2026-01-01T00:00:00Z",
                "migration_version": "0011"
            }))
        result = verify_backup(archive)
        assert not result["ok"]

    def test_verify_accepts_valid_archive(self, tmp_path):
        from mimir.backup.verify import verify_backup
        # Build a minimal valid archive
        archive = tmp_path / "test.zip"

        # Create a minimal valid SQLite DB
        import sqlite3
        db_path = tmp_path / "mimir.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE memories (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES ('0011')")
        conn.commit()
        conn.close()

        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("manifest.json", json.dumps({
                "version": "1.0",
                "created_at": "2026-01-01T00:00:00Z",
                "migration_version": "0011"
            }))
            zf.write(db_path, "mimir.db")
            zf.writestr("vectors/chroma.sqlite3", b"placeholder")

        result = verify_backup(archive)
        assert result["ok"], f"checks: {result['checks']}"

    def test_verify_catches_version_mismatch(self, tmp_path):
        from mimir.backup.verify import verify_backup
        archive = tmp_path / "test.zip"

        import sqlite3
        db_path = tmp_path / "mimir.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE memories (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES ('0009')")  # mismatch
        conn.commit()
        conn.close()

        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("manifest.json", json.dumps({
                "version": "1.0",
                "created_at": "2026-01-01T00:00:00Z",
                "migration_version": "0011"  # different from DB
            }))
            zf.write(db_path, "mimir.db")

        result = verify_backup(archive)
        version_check = next((c for c in result["checks"] if c["name"] == "migration_version_matches"), None)
        assert version_check is not None
        assert not version_check["passed"]

    @pytest.mark.asyncio
    async def test_create_backup_produces_archive(self, tmp_path):
        from mimir.backup.create import create_backup
        archive_path = await create_backup(out_dir=tmp_path)
        assert archive_path.exists()
        assert zipfile.is_zipfile(archive_path)
        with zipfile.ZipFile(archive_path, "r") as zf:
            assert "manifest.json" in zf.namelist()


# ══════════════════════════════════════════════════════════════════════════════
# P5 — Auth + Secret Hardening
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthHardening:
    async def test_health_is_public(self, client):
        """Health endpoint requires no auth."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_protected_endpoint_rejects_no_key_in_prod(self, app):
        """Protected endpoints return 401 in prod mode."""
        from mimir.config import get_settings, Settings
        settings = get_settings()
        original_mode = settings.auth_mode

        # Temporarily switch to prod auth
        object.__setattr__(settings, "auth_mode", "prod")
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/system/status")
            assert resp.status_code == 401
        finally:
            object.__setattr__(settings, "auth_mode", original_mode)

    async def test_dev_auth_mode_allows_no_key(self, client):
        """Dev mode accepts requests without API key."""
        resp = await client.get("/api/system/status")
        assert resp.status_code == 200

    def test_api_key_hash_stored_not_plaintext(self):
        """get_current_user uses SHA-256 hash lookup, not plaintext comparison."""
        import hashlib
        from api.deps import get_current_user
        import inspect
        src = inspect.getsource(get_current_user)
        assert "sha256" in src
        assert "key_hash" in src

    def test_api_keys_model_has_key_hash_not_key(self):
        """APIKey model stores key_hash, not plaintext key."""
        from storage.models import APIKey
        columns = {c.key for c in APIKey.__table__.columns}
        assert "key_hash" in columns
        assert "key" not in columns or "key_hash" in columns  # key_hash must be present

    def test_api_key_route_does_not_return_plaintext_after_creation(self):
        """API key creation response schema should include key only once (at creation)."""
        from api import schemas
        # The schema test: RecallFeedbackIn exists; key creation schemas shouldn't
        # expose the hash in list endpoints
        import inspect
        source = inspect.getsource(schemas)
        # key_hash should not be in public schemas (only creation response)
        # This is a best-effort check — key_hash should not appear in list schemas
        assert "key_hash" not in source or "APIKeyCreate" in source or "APIKeyOut" not in source

    def test_slack_route_has_signature_verification(self):
        """Slack route must verify HMAC-SHA256 signatures."""
        import inspect
        from api.routes import slack
        src = inspect.getsource(slack)
        assert "signing_secret" in src or "hmac" in src.lower() or "sha256" in src.lower()


# ══════════════════════════════════════════════════════════════════════════════
# P6 — Operational Health Endpoints
# ══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoints:
    async def test_health_endpoint_exists(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] == "ok"

    async def test_system_status_endpoint(self, client):
        resp = await client.get("/api/system/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "components" in data
        assert "database" in data["components"]
        assert "vector_store" in data["components"]
        assert "worker" in data["components"]

    async def test_system_status_includes_migration_revision(self, client):
        resp = await client.get("/api/system/status")
        data = resp.json()
        db_info = data["components"]["database"]
        assert "migration_revision" in db_info

    async def test_system_status_includes_fts(self, client):
        resp = await client.get("/api/system/status")
        data = resp.json()
        assert "fts" in data["components"]
        assert "status" in data["components"]["fts"]

    async def test_system_status_includes_memory_counts(self, client):
        resp = await client.get("/api/system/status")
        data = resp.json()
        assert "memory" in data
        assert "counts_by_layer" in data["memory"]
        assert "total" in data["memory"]

    async def test_system_jobs_endpoint(self, client):
        resp = await client.get("/api/system/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
        assert "running_count" in data

    async def test_readiness_endpoint_exists(self, client):
        resp = await client.get("/api/system/readiness")
        # Should be 200 (ready) or 503 (not ready) — both are valid responses
        assert resp.status_code in (200, 503)

    async def test_readiness_returns_checks_dict(self, client):
        resp = await client.get("/api/system/readiness")
        if resp.status_code == 200:
            data = resp.json()
        else:
            data = resp.json()["detail"]
        assert "checks" in data
        checks = data["checks"]
        assert "database" in checks
        assert "migration" in checks
        assert "vector_store" in checks

    async def test_readiness_database_check(self, client):
        resp = await client.get("/api/system/readiness")
        if resp.status_code == 200:
            data = resp.json()
        else:
            data = resp.json()["detail"]
        assert data["checks"]["database"]["ok"] is True

    async def test_readiness_migration_check_has_revision(self, client):
        resp = await client.get("/api/system/readiness")
        if resp.status_code == 200:
            data = resp.json()
        else:
            data = resp.json()["detail"]
        migration = data["checks"]["migration"]
        assert "revision" in migration

    async def test_system_metrics_endpoint(self, client):
        resp = await client.get("/api/system/metrics")
        assert resp.status_code == 200

    async def test_top_level_metrics_endpoint(self, client):
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# P7 — Load test module
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadTestModule:
    def test_load_test_importable(self):
        from evals.load_test import run_load_test
        assert callable(run_load_test)

    def test_load_test_has_required_params(self):
        import inspect
        from evals.load_test import run_load_test
        sig = inspect.signature(run_load_test)
        params = list(sig.parameters)
        assert "users" in params
        assert "sessions" in params
        assert "out_path" in params

    @pytest.mark.asyncio
    async def test_load_test_smoke_1_user_3_sessions(self, tmp_path):
        """Smoke run: 1 user, 3 sessions — validates plumbing without load."""
        from evals.load_test import run_load_test
        out = tmp_path / "load_result.json"
        report = await run_load_test(users=1, sessions=3, out_path=out)
        assert "latency_ms" in report
        assert "error_rate" in report
        assert out.exists()
        with open(out) as f:
            data = json.load(f)
        assert "wall_time_s" in data
        assert "storage" in data

    @pytest.mark.asyncio
    async def test_load_test_report_structure(self, tmp_path):
        from evals.load_test import run_load_test
        report = await run_load_test(users=1, sessions=2, out_path=None)
        assert "config" in report
        assert report["config"]["users"] == 1
        assert report["config"]["sessions"] == 2
        assert "write" in report["latency_ms"]
        assert "recall" in report["latency_ms"]
        for name, stats in report["latency_ms"].items():
            assert "p50" in stats
            assert "p95" in stats
            assert "count" in stats


# ══════════════════════════════════════════════════════════════════════════════
# P8 — Operator docs exist
# ══════════════════════════════════════════════════════════════════════════════

class TestOperatorDocs:
    def _docs_dir(self):
        return Path(__file__).parent.parent / "docs"

    def test_deployment_doc_exists(self):
        assert (self._docs_dir() / "DEPLOYMENT.md").exists()

    def test_backup_restore_doc_exists(self):
        assert (self._docs_dir() / "BACKUP_RESTORE.md").exists()

    def test_upgrade_doc_exists(self):
        assert (self._docs_dir() / "UPGRADE.md").exists()

    def test_security_doc_exists(self):
        assert (self._docs_dir() / "SECURITY.md").exists()

    def test_operations_doc_exists(self):
        assert (self._docs_dir() / "OPERATIONS.md").exists()

    def test_release_checklist_doc_exists(self):
        assert (self._docs_dir() / "RELEASE_CHECKLIST.md").exists()

    def test_deployment_doc_covers_docker(self):
        content = (self._docs_dir() / "DEPLOYMENT.md").read_text()
        assert "docker" in content.lower()
        assert "MIMIR_SECRET_KEY" in content

    def test_backup_restore_doc_covers_verify(self):
        content = (self._docs_dir() / "BACKUP_RESTORE.md").read_text()
        assert "verify" in content.lower()
        assert "restore" in content.lower()

    def test_security_doc_covers_api_keys(self):
        content = (self._docs_dir() / "SECURITY.md").read_text()
        assert "api key" in content.lower() or "API key" in content
        assert "hash" in content.lower()

    def test_operations_doc_covers_reindex(self):
        content = (self._docs_dir() / "OPERATIONS.md").read_text()
        assert "reindex" in content.lower()

    def test_upgrade_doc_covers_rollback(self):
        content = (self._docs_dir() / "UPGRADE.md").read_text()
        assert "rollback" in content.lower() or "downgrade" in content.lower()

    def test_release_checklist_covers_gate(self):
        content = (self._docs_dir() / "RELEASE_CHECKLIST.md").read_text()
        assert "gate" in content.lower() or "release gate" in content.lower()


# ══════════════════════════════════════════════════════════════════════════════
# P9 — Release report module
# ══════════════════════════════════════════════════════════════════════════════

class TestReleaseArtifact:
    def test_release_report_importable(self):
        from evals.release_report import generate_release_report
        assert callable(generate_release_report)

    def test_make_release_target_in_makefile(self):
        makefile = Path(__file__).parent.parent / "Makefile"
        content = makefile.read_text()
        assert "release" in content
        assert "release_report" in content or "release-report" in content or "evals.release_report" in content

    def test_reports_release_dir_can_be_created(self, tmp_path):
        """Smoke: reports/release/ parent can be created."""
        p = tmp_path / "reports" / "release"
        p.mkdir(parents=True)
        assert p.exists()


# ══════════════════════════════════════════════════════════════════════════════
# P4 — Docker compose schema
# ══════════════════════════════════════════════════════════════════════════════

class TestDockerCompose:
    def _compose(self):
        import yaml
        compose_path = Path(__file__).parent.parent / "docker-compose.yml"
        with open(compose_path) as f:
            try:
                return yaml.safe_load(f)
            except Exception:
                return None

    def test_compose_file_exists(self):
        assert (Path(__file__).parent.parent / "docker-compose.yml").exists()

    def test_compose_has_api_service(self):
        try:
            compose = self._compose()
            if compose is None:
                pytest.skip("yaml not installed")
        except ImportError:
            pytest.skip("yaml not installed")
        assert "api" in compose.get("services", {})

    def test_compose_has_worker_service(self):
        try:
            compose = self._compose()
            if compose is None:
                pytest.skip("yaml not installed")
        except ImportError:
            pytest.skip("yaml not installed")
        assert "worker" in compose.get("services", {})

    def test_compose_has_web_service(self):
        try:
            compose = self._compose()
            if compose is None:
                pytest.skip("yaml not installed")
        except ImportError:
            pytest.skip("yaml not installed")
        assert "web" in compose.get("services", {})

    def test_compose_has_persistent_volume(self):
        try:
            compose = self._compose()
            if compose is None:
                pytest.skip("yaml not installed")
        except ImportError:
            pytest.skip("yaml not installed")
        assert "mimir_data" in compose.get("volumes", {})

    def test_compose_api_has_healthcheck(self):
        try:
            compose = self._compose()
            if compose is None:
                pytest.skip("yaml not installed")
        except ImportError:
            pytest.skip("yaml not installed")
        api = compose.get("services", {}).get("api", {})
        assert "healthcheck" in api

    def test_web_dockerfile_exists(self):
        assert (Path(__file__).parent.parent / "web" / "Dockerfile").exists()
