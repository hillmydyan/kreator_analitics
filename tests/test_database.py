"""Unit tests for DatabaseManager covering PRD Section 3.5, 4.8 & Section 6."""

import unittest

from shadowkreator.core.database import DatabaseManager, normalize_video_url
from shadowkreator.core.models import (
    ConnectionType,
    DeviceInfo,
    DeviceStatus,
    ExtractionStatus,
    JobStatus,
    LogLevel,
    ScrapingJobRecord,
    VideoProductRecord,
    VideoRecord,
)


class TestDatabaseManager(unittest.TestCase):
    def setUp(self) -> None:
        self.db = DatabaseManager(":memory:")

    def test_device_upsert_and_list(self) -> None:
        dev = DeviceInfo(
            device_serial="R58R123456A",
            device_name="Samsung Galaxy A52",
            model="SM-A525F",
            android_version="13",
            connection_type=ConnectionType.USB.value,
            connection_status=DeviceStatus.CONNECTED.value,
        )
        saved = self.db.upsert_device(dev)
        self.assertIsNotNone(saved.id)

        # Update status to Disconnected
        dev.connection_status = DeviceStatus.DISCONNECTED.value
        self.db.upsert_device(dev)

        devices = self.db.list_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].connection_status, DeviceStatus.DISCONNECTED.value)
        self.assertEqual(devices[0].device_name, "Samsung Galaxy A52")

    def test_video_deduplication_and_non_destructive_history(self) -> None:
        job1 = self.db.create_job(
            ScrapingJobRecord(
                creator_username="@creatorname",
                period_days=7,
                start_date="2026-11-11",
                end_date="2026-11-18",
                started_at="2026-11-18T10:00:00Z",
                job_status=JobStatus.SCANNING_VIDEOS.value,
            )
        )
        job2 = self.db.create_job(
            ScrapingJobRecord(
                creator_username="@creatorname",
                period_days=30,
                start_date="2026-10-19",
                end_date="2026-11-18",
                started_at="2026-11-18T11:00:00Z",
                job_status=JobStatus.SCANNING_VIDEOS.value,
            )
        )

        # Duplicate video within job1 should update rather than duplicate
        v1 = self.db.save_video(
            VideoRecord(
                job_id=job1.id,
                creator_username="@creatorname",
                video_id="7400001",
                video_url="https://www.tiktok.com/@creatorname/video/7400001?is_from_webapp=1",
                upload_date="2026-11-15",
                views=10000,
                has_product=True,
                product_count=1,
                scraped_at="2026-11-18T10:01:00Z",
            )
        )
        v1_dup = self.db.save_video(
            VideoRecord(
                job_id=job1.id,
                creator_username="@creatorname",
                video_id="7400001",
                video_url="https://www.tiktok.com/@creatorname/video/7400001",
                upload_date="2026-11-15",
                views=12500,
                has_product=True,
                product_count=2,
                scraped_at="2026-11-18T10:02:00Z",
            )
        )
        self.assertEqual(v1.id, v1_dup.id)
        self.assertEqual(len(self.db.list_videos(job1.id)), 1)
        self.assertEqual(self.db.list_videos(job1.id)[0].views, 12500)

        # Same video in job2 must NOT overwrite job1's history (PRD Section 4.8)
        v2 = self.db.save_video(
            VideoRecord(
                job_id=job2.id,
                creator_username="@creatorname",
                video_id="7400001",
                video_url="https://www.tiktok.com/@creatorname/video/7400001",
                upload_date="2026-11-15",
                views=20000,
                has_product=True,
                product_count=2,
                scraped_at="2026-11-18T11:02:00Z",
            )
        )
        self.assertNotEqual(v1.id, v2.id)
        self.assertEqual(self.db.list_videos(job1.id)[0].views, 12500)
        self.assertEqual(self.db.list_videos(job2.id)[0].views, 20000)

    def test_product_grouping_rules_prd_3_5(self) -> None:
        # 1. Same name + DIFFERENT links -> must NOT be merged (PRD 3.5)
        p_link1 = self.db.resolve_or_create_product(
            "Serum Wajah Glowing", "https://shop.tiktok.com/view/product/111"
        )
        p_link2 = self.db.resolve_or_create_product(
            "  serum wajah   glowing ", "https://shop.tiktok.com/view/product/222"
        )
        self.assertNotEqual(p_link1.id, p_link2.id)
        self.assertFalse(p_link1.is_ambiguous)
        self.assertFalse(p_link2.is_ambiguous)

        # 2. Same name + SAME link -> merged into existing product
        p_link1_again = self.db.resolve_or_create_product(
            "SERUM WAJAH GLOWING", "https://shop.tiktok.com/view/product/111?utm_source=copy"
        )
        self.assertEqual(p_link1.id, p_link1_again.id)

        # 3. Same name + NO link when URL-bearing products exist -> flagged as ambiguous!
        p_no_link = self.db.resolve_or_create_product("Serum Wajah Glowing", None)
        self.assertNotEqual(p_link1.id, p_no_link.id)
        self.assertTrue(p_no_link.is_ambiguous)

    def test_video_products_and_logs(self) -> None:
        job = self.db.create_job(
            ScrapingJobRecord(
                creator_username="@creatorname",
                period_days=7,
                start_date="2026-11-11",
                end_date="2026-11-18",
                started_at="2026-11-18T10:00:00Z",
            )
        )
        video = self.db.save_video(
            VideoRecord(
                job_id=job.id,
                creator_username="@creatorname",
                video_id="7400099",
                video_url="https://www.tiktok.com/@creatorname/video/7400099",
                upload_date="2026-11-16",
                views=50000,
                has_product=True,
                product_count=1,
                scraped_at="2026-11-18T10:05:00Z",
            )
        )
        prod = self.db.resolve_or_create_product("Moisturizer Ceramide", None)
        vp = self.db.link_video_product(
            VideoProductRecord(
                job_id=job.id,
                video_id=video.id,
                product_id=prod.id,
                views_snapshot=50000,
                extraction_status=ExtractionStatus.LINK_UNAVAILABLE.value,
                scraped_at="2026-11-18T10:05:00Z",
            )
        )
        self.assertIsNotNone(vp.id)

        self.db.add_log(
            job.id,
            LogLevel.WARNING.value,
            "extract_product",
            "Link produk tidak tersedia di tampilan UI",
            video_id=video.video_id,
        )
        logs = self.db.list_logs(job.id)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].video_id, "7400099")


if __name__ == "__main__":
    unittest.main()
