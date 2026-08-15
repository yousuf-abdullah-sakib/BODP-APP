from app.worker.celery_app import celery_app, ingestion_soft_time_limit_seconds


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


class TestIngestionQueueRouting:
    def test_process_dataset_file_routed_to_ingestion_queue(self):
        routes = celery_app.conf.task_routes
        assert routes["ingestion.process_dataset_file"]["queue"] == "ingestion"
