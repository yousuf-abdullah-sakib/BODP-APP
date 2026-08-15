from app.worker.celery_app import (
    celery_app,
    ingestion_hard_time_limit_seconds,
    ingestion_soft_time_limit_seconds,
)


class TestIngestionSoftTimeLimit:
    def test_scales_with_file_size(self):
        small = ingestion_soft_time_limit_seconds(1024)
        one_gb = ingestion_soft_time_limit_seconds(1024**3)
        ten_gb = ingestion_soft_time_limit_seconds(10 * 1024**3)

        assert small < one_gb < ten_gb

    def test_none_size_uses_baseline_only(self):
        from app.core.config import settings

        assert ingestion_soft_time_limit_seconds(None) == settings.INGESTION_SOFT_TIME_LIMIT_BASE_SECONDS

    def test_zero_size_uses_baseline_only(self):
        from app.core.config import settings

        assert ingestion_soft_time_limit_seconds(0) == settings.INGESTION_SOFT_TIME_LIMIT_BASE_SECONDS


class TestIngestionSoftTimeLimitMaxCap:
    def test_unset_max_leaves_large_files_uncapped(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "INGESTION_SOFT_TIME_LIMIT_MAX_SECONDS", None)
        # A genuinely huge file's computed limit is returned as-is, not
        # silently clamped to some other default — "configurable ceiling
        # that defaults to unlimited" is the whole point of this setting.
        huge = ingestion_soft_time_limit_seconds(500 * 1024**3)
        assert huge > 10_000

    def test_configured_max_caps_the_computed_value(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "INGESTION_SOFT_TIME_LIMIT_MAX_SECONDS", 3600)
        # Without the cap this would be far larger than 3600.
        assert ingestion_soft_time_limit_seconds(500 * 1024**3) == 3600

    def test_configured_max_does_not_raise_a_small_files_limit(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "INGESTION_SOFT_TIME_LIMIT_MAX_SECONDS", 3600)
        # The cap only ever lowers, never raises, the computed value.
        small = ingestion_soft_time_limit_seconds(1024)
        assert small < 3600

    def test_empty_string_env_var_parses_as_unlimited(self, monkeypatch):
        """docker-compose.prod.yml's ${VAR:-} syntax always sets the key,
        to an empty string when unconfigured — Settings must treat that
        the same as never having set it (unlimited), not raise a
        validation error at startup."""
        import os

        from app.core.config import Settings

        monkeypatch.setenv("INGESTION_SOFT_TIME_LIMIT_MAX_SECONDS", "")
        s = Settings()
        assert s.INGESTION_SOFT_TIME_LIMIT_MAX_SECONDS is None
        os.environ.pop("INGESTION_SOFT_TIME_LIMIT_MAX_SECONDS", None)


class TestIngestionHardTimeLimit:
    def test_hard_limit_always_exceeds_soft_limit(self):
        for size in (0, 1024, 1024**3, 100 * 1024**3):
            assert ingestion_hard_time_limit_seconds(size) > ingestion_soft_time_limit_seconds(size)

    def test_hard_limit_grace_matches_configured_setting(self):
        from app.core.config import settings

        size = 5 * 1024**3
        expected_grace = settings.INGESTION_HARD_TIME_LIMIT_GRACE_SECONDS
        assert (
            ingestion_hard_time_limit_seconds(size) - ingestion_soft_time_limit_seconds(size)
            == expected_grace
        )


class TestIngestionQueueRouting:
    def test_process_dataset_file_routed_to_ingestion_queue(self):
        routes = celery_app.conf.task_routes
        assert routes["ingestion.process_dataset_file"]["queue"] == "ingestion"
