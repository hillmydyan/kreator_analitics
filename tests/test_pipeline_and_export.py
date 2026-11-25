"""
Integration and unit tests for Tahap 2 (PoC Android UI), Tahap 3 (Video Extraction),
Tahap 4 (Product Extraction), Tahap 5 (Analytics Engine), and Tahap 7 (Excel Export & Resume).
"""

import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from shadowkreator.core.analytics import AnalyticsEngine
from shadowkreator.core.controller import ScrapingController
from shadowkreator.core.database import DatabaseManager
from shadowkreator.core.extractor import is_within_period, parse_upload_date, parse_views_count
from shadowkreator.core.exporter import ExportManager, build_export_filename
from shadowkreator.core.models import ConnectionType, DeviceInfo, DeviceStatus, JobStatus
from shadowkreator.core.ui_automation import (
    MockUiDriver,
    RawProductItem,
    RawVideoItem,
    detect_login_or_verification_wall,
    parse_ui_hierarchy_xml,
    parse_video_and_products_from_nodes,
)


class TestStages2To7Pipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.db = DatabaseManager(":memory:")
        self.controller = ScrapingController(self.db)
        self.analytics = AnalyticsEngine(self.db)
        self.exporter = ExportManager(self.db)
        self.device = DeviceInfo(
            device_serial="SIMULATOR-ANDROID-13",
            device_name="Samsung Galaxy A52 (Simulasi)",
            model="SM-A525F",
            android_version="13",
            connection_type=ConnectionType.USB.value,
            connection_status=DeviceStatus.CONNECTED.value,
            sdk_int=33,
        )

    def test_stage2_ui_hierarchy_xml_parsing_and_login_wall(self) -> None:
        sample_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
        <hierarchy rotation="0">
          <node index="0" text="@creator_affiliate" resource-id="com.ss.android.ugc.trill:id/title"
                class="android.widget.TextView" package="com.ss.android.ugc.trill"
                content-desc="" clickable="false" bounds="[40,120][400,180]" />
          <node index="1" text="250.5K tayangan" resource-id="com.ss.android.ugc.trill:id/view_count"
                class="android.widget.TextView" package="com.ss.android.ugc.trill"
                content-desc="" clickable="false" bounds="[40,800][300,850]" />
          <node index="2" text="2 hari lalu" resource-id="com.ss.android.ugc.trill:id/upload_date"
                class="android.widget.TextView" package="com.ss.android.ugc.trill"
                content-desc="" clickable="false" bounds="[310,800][500,850]" />
          <node index="3" text="Produk: Serum Wajah Glowing" resource-id="com.ss.android.ugc.trill:id/anchor_title"
                class="android.widget.TextView" package="com.ss.android.ugc.trill"
                content-desc="Keranjang Kuning" clickable="true" bounds="[40,900][600,960]" />
        </hierarchy>
        """
        nodes = parse_ui_hierarchy_xml(sample_xml)
        self.assertEqual(len(nodes), 4)
        self.assertEqual(nodes[3].center, (320, 930))

        wall, _ = detect_login_or_verification_wall(nodes)
        self.assertFalse(wall)

        extracted = parse_video_and_products_from_nodes(nodes, "@creator_affiliate")
        self.assertEqual(extracted.raw_views, "250.5K")
        self.assertEqual(extracted.raw_upload_date, "2 hari lalu")
        self.assertTrue(extracted.has_yellow_basket)
        self.assertEqual(len(extracted.products), 1)
        self.assertEqual(extracted.products[0].product_name, "Serum Wajah Glowing")

    def test_stage3_views_and_date_parsing_no_estimation(self) -> None:
        self.assertEqual(parse_views_count("250.5K"), 250_500)
        self.assertEqual(parse_views_count("1.2M"), 1_200_000)
        self.assertEqual(parse_views_count("15,4rb tayangan"), 15_400)
        self.assertEqual(parse_views_count("1.000.000"), 1_000_000)
        # Missing/unparseable must return None, never guessed (PRD Section 7.3)
        self.assertIsNone(parse_views_count(""))
        self.assertIsNone(parse_views_count("tidak terbaca"))

        ref = date(2026, 11, 18)
        self.assertEqual(parse_upload_date("2026-11-15", ref), date(2026, 11, 15))
        self.assertEqual(parse_upload_date("3 hari lalu", ref), date(2026, 11, 15))
        self.assertEqual(parse_upload_date("2d ago", ref), date(2026, 11, 16))
        self.assertIsNone(parse_upload_date("tanggal rusak", ref))

        in_7d, _, _ = is_within_period(date(2026, 11, 15), 7, ref)
        self.assertTrue(in_7d)
        out_7d, _, _ = is_within_period(date(2026, 11, 3), 7, ref)
        self.assertFalse(out_7d)
        in_30d, _, _ = is_within_period(date(2026, 11, 3), 30, ref)
        self.assertTrue(in_30d)

    def test_full_scraping_7d_and_30d_analytics_and_excel_export(self) -> None:
        driver = MockUiDriver()
        ref = date(2026, 11, 18)
        snapshots = []

        # 1. Run 7-day scraping job
        job_7d = self.controller.run_scraping_job(
            creator_username="@creator_affiliate",
            period_days=7,
            device=self.device,
            ui_driver=driver,
            compliance_accepted=True,
            reference_date=ref,
            on_progress=snapshots.append,
        )
        # In MockUiDriver: 3 videos are within 7 days (1d, 3d, 5d ago); 15d & 45d are filtered out
        self.assertEqual(job_7d.total_videos, 3)
        self.assertEqual(job_7d.processed_videos, 3)
        self.assertEqual(job_7d.job_status, JobStatus.COMPLETED_WITH_WARNINGS.value)
        self.assertTrue(len(snapshots) > 0)
        self.assertEqual(snapshots[-1].progress_percent, 100)

        # 2. Verify Analytics Engine calculations (PRD Section 3.6)
        report_7d = self.analytics.build_job_report(job_7d.id)
        self.assertEqual(report_7d.total_videos, 3)
        # Total creator views = 250,500 + 180,000 + 45,200 = 475,700
        self.assertEqual(report_7d.total_views, 475_700)
        self.assertEqual(report_7d.total_unique_products, 3)

        # Top product: Serum Wajah Glowing Niacinamide 10% appears in Video 1 (250,500) & Video 2 (180,000)
        top_prod = report_7d.products[0]
        self.assertEqual(top_prod.product_name, "Serum Wajah Glowing Niacinamide 10%")
        self.assertEqual(top_prod.video_count, 2)
        self.assertEqual(top_prod.total_views, 430_500)
        self.assertEqual(top_prod.average_views, 215_250.0)
        # Contribution = 430,500 / 475,700 * 100 = 90.5%
        self.assertAlmostEqual(top_prod.view_contribution_pct, 90.50, places=2)

        # Note that sum of product views (430,500 + 250,500 + 250,500 = 931,500) > creator_total_views (475,700)
        # as documented in PRD Section 3.6!
        sum_product_views = sum(p.total_views for p in report_7d.products)
        self.assertGreater(sum_product_views, report_7d.total_views)

        # 3. Run 30-day scraping job and verify 4th video (15 days ago) + non-merged product with different URL
        job_30d = self.controller.run_scraping_job(
            creator_username="@creator_affiliate",
            period_days=30,
            device=self.device,
            ui_driver=driver,
            compliance_accepted=True,
            reference_date=ref,
        )
        report_30d = self.analytics.build_job_report(job_30d.id)
        self.assertEqual(report_30d.total_videos, 4)
        # Video 4 has a Serum with the SAME name but DIFFERENT URL (99999999 vs 17290001)
        # Per PRD 3.5, it must NOT be merged into Serum #1 -> 4 unique products total!
        self.assertEqual(report_30d.total_unique_products, 4)

        # 4. Verify Excel Export (PRD Section 4.9: 4 worksheets)
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / build_export_filename("@creator_affiliate", 30, ref)
            saved_path = self.exporter.export_job_to_excel(job_30d.id, out_file)
            self.assertTrue(saved_path.is_file())
            self.assertEqual(
                saved_path.name,
                "TikTok_Analytics_creator_affiliate_30d_2026-11-18.xlsx",
            )
            with zipfile.ZipFile(saved_path, "r") as zf:
                wb_xml = zf.read("xl/workbook.xml").decode("utf-8")
                self.assertIn("Creator Summary", wb_xml)
                self.assertIn("Product Summary", wb_xml)
                self.assertIn("Video Details", wb_xml)
                self.assertIn("Extraction Logs", wb_xml)

    def test_interrupted_job_and_resume(self) -> None:
        """Verify PRD Section 7 & MVP 5: interrupted job saves partial data and can be resumed."""

        class FlakyDriver(MockUiDriver):
            def __init__(self) -> None:
                super().__init__()
                self.fail_on_second = True

            def collect_creator_videos(self, creator_username, period_days, reference_date=None):
                items = super().collect_creator_videos(creator_username, period_days, reference_date)
                if self.fail_on_second:
                    # Simulate connection loss right after 1st video by raising inside iteration
                    raise ConnectionError("Kabel USB terputus saat membaca daftar video")
                return items

        flaky = FlakyDriver()
        ref = date(2026, 11, 18)

        interrupted_job = self.controller.run_scraping_job(
            creator_username="@creator_affiliate",
            period_days=7,
            device=self.device,
            ui_driver=flaky,
            reference_date=ref,
        )
        self.assertEqual(interrupted_job.job_status, JobStatus.INTERRUPTED.value)

        # Now restore connection and resume the interrupted job
        flaky.fail_on_second = False
        resumed_job = self.controller.run_scraping_job(
            creator_username="@creator_affiliate",
            period_days=7,
            device=self.device,
            ui_driver=flaky,
            reference_date=ref,
            resume_job_id=interrupted_job.id,
        )
        self.assertEqual(resumed_job.id, interrupted_job.id)
        self.assertEqual(resumed_job.processed_videos, 3)
        self.assertIn(
            resumed_job.job_status,
            (JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_WARNINGS.value),
        )

    def test_stage4_yellow_basket_product_url_extraction(self) -> None:
        """Verify PRD 3.4 product URL extraction from yellow basket activity intent / logcat."""
        from shadowkreator.core.device_manager import AdbCommandResult
        from shadowkreator.core.ui_automation import (
            AdbUiDriver,
            RawProductItem,
            RawVideoItem,
            UiNode,
            extract_product_urls_from_text,
        )

        sample_intent = (
            "Intent { dat=aweme://echybrid?url=https%3A%2F%2Foec-api.tiktokv.com%2Fview"
            "&trackParams=%7B%22product_id%22%3A%221729543210987654321%22%7D }"
        )
        urls = extract_product_urls_from_text(sample_intent)
        self.assertIn(
            "https://shop.tiktok.com/view/product/1729543210987654321",
            urls,
        )

        tapped_commands = []

        def fake_runner(cmd, timeout=10):
            joined = " ".join(cmd)
            tapped_commands.append(joined)
            if "dumpsys activity top" in joined and not any("input tap" in c for c in tapped_commands):
                return AdbCommandResult(0, "ACTIVITY com.ss.android.ugc.trill/.detail.DetailActivity", "")
            if "dumpsys activity top" in joined:
                return AdbCommandResult(0, sample_intent, "")
            return AdbCommandResult(0, "OK", "")

        driver = AdbUiDriver("192.168.1.40:45459", adb_path="/usr/bin/adb", runner=fake_runner)
        video_nodes = [
            UiNode(
                text="M231 Kemeja Pria Koko Couple P",
                resource_id="com.ss.android.ugc.trill:id/b6g",
                bounds=(32, 1180, 420, 1240),
            )
        ]
        item = RawVideoItem(
            video_id="7410009999",
            video_url="https://www.tiktok.com/@kakmuti7/video/7410009999",
            raw_upload_date="2d ago",
            raw_views="286",
            has_yellow_basket=True,
            products=[RawProductItem(product_name="M231 Kemeja Pria Koko Couple P")],
        )
        driver._enrich_products_via_yellow_basket(item, video_nodes, 1)
        self.assertEqual(
            item.products[0].product_url,
            "https://shop.tiktok.com/view/product/1729543210987654321",
        )

    def test_multi_product_yellow_basket_and_misclick_validation(self) -> None:
        """
        Verify multi-product yellow basket (2+ products) detection & sheet extraction,
        safe yellow-basket tap coordinate bounds, and window-token identity comparison.
        """
        from shadowkreator.core.device_manager import AdbCommandResult
        from shadowkreator.core.ui_automation import (
            AdbUiDriver,
            RawProductItem,
            RawVideoItem,
            UiNode,
            _detect_multi_product_count_from_video_nodes,
            _extract_window_token,
            _find_yellow_basket_tap_target,
        )

        # 1. Window token comparison must ignore changing Surface numbers across dialog open/close
        focus_a = "mCurrentFocus=Window{7262107 u0 com.ss.android.ugc.trill/com.ss.android.ugc.profile.platform.framework.aweme.profile.ui.UserProfileHostActivity}-[Surface(name=*Title#6585)/@0x8cb940c]"
        focus_b = "mCurrentFocus=Window{7262107 u0 com.ss.android.ugc.trill/com.ss.android.ugc.profile.platform.framework.aweme.profile.ui.UserProfileHostActivity}-[Surface(name=*Title#6657)/@0x88831a9]"
        focus_dialog = "mCurrentFocus=Window{279c32c u0 com.ss.android.ugc.trill/com.ss.android.ugc.aweme.detail.ui.DetailActivity}-[Surface(name=*Title#6516)/@0x9b1e656]"
        self.assertEqual(_extract_window_token(focus_a), _extract_window_token(focus_b))
        self.assertNotEqual(_extract_window_token(focus_a), _extract_window_token(focus_dialog))

        # 2. Safe yellow-basket coordinate bounds must reject right-side action buttons (x > 570)
        unsafe_nodes = [
            UiNode(
                text="Share video",
                resource_id="com.ss.android.ugc.trill:id/b_w",
                bounds=(592, 1270, 720, 1390),
            )
        ]
        self.assertIsNone(_find_yellow_basket_tap_target(unsafe_nodes))

        # 3. Multi-product badge detection (2+ products in yellow basket)
        multi_video_nodes = [
            UiNode(
                text="",
                resource_id="com.ss.android.ugc.trill:id/feed_multi_tag_layout",
                bounds=(24, 1298, 510, 1358),
            ),
            UiNode(
                text="Koko Sultan Jaguar Kemeja Koko · 2",
                resource_id="com.ss.android.ugc.trill:id/b_w",
                bounds=(86, 1308, 498, 1348),
            ),
        ]
        self.assertEqual(_detect_multi_product_count_from_video_nodes(multi_video_nodes), 2)

        # 4. Multi-product sheet extraction must collect all 2+ products and NEVER tap Share (484, 506) or Copy Link (92, 1445)
        multi_sheet_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
        <hierarchy rotation="0">
          <node index="0" text="Produk di video ini (2)" resource-id="com.ss.android.ugc.trill:id/sheet_header"
                class="android.widget.TextView" package="com.ss.android.ugc.trill"
                content-desc="" clickable="false" bounds="[32,420][400,470]" />
          <node index="1" text="Koko Sultan Jaguar Kemeja Koko" resource-id="com.ss.android.ugc.trill:id/tv_product_name"
                class="android.widget.TextView" package="com.ss.android.ugc.trill"
                content-desc="" clickable="true" bounds="[180,520][560,570]" />
          <node index="2" text="Rp129.400" resource-id="com.ss.android.ugc.trill:id/tv_price"
                class="android.widget.TextView" package="com.ss.android.ugc.trill"
                content-desc="" clickable="false" bounds="[180,580][320,620]" />
          <node index="3" text="Celana Chino Panjang Pria Slimfit" resource-id="com.ss.android.ugc.trill:id/tv_product_name"
                class="android.widget.TextView" package="com.ss.android.ugc.trill"
                content-desc="" clickable="true" bounds="[180,720][560,770]" />
          <node index="4" text="Beli" resource-id="com.ss.android.ugc.trill:id/btn_buy"
                class="android.widget.TextView" package="com.ss.android.ugc.trill"
                content-desc="" clickable="true" bounds="[580,730][680,780]" />
        </hierarchy>
        """
        tapped_cmds = []

        def fake_multi_runner(cmd, timeout=10):
            joined = " ".join(cmd)
            tapped_cmds.append(joined)
            if "dumpsys activity top" in joined:
                return AdbCommandResult(
                    0,
                    "ACTIVITY com.ss.android.ugc.trill/.detail.DetailActivity MultiProductAnchorSheet",
                    "",
                )
            if "uiautomator dump" in joined:
                return AdbCommandResult(0, "UI hierchary dumped to: /sdcard/shadowkreator_ui.xml", "")
            if "cat /sdcard/shadowkreator_ui.xml" in joined:
                return AdbCommandResult(0, multi_sheet_xml, "")
            return AdbCommandResult(0, "OK", "")

        driver = AdbUiDriver("192.168.1.12:33193", adb_path="/usr/bin/adb", runner=fake_multi_runner)
        multi_item = RawVideoItem(
            video_id="7410008888",
            video_url="https://www.tiktok.com/@kakmuti7/video/7410008888",
            raw_upload_date="1h ago",
            raw_views="210",
            has_yellow_basket=True,
            products=[RawProductItem(product_name="Koko Sultan Jaguar Kemeja Koko")],
        )
        driver._enrich_products_via_yellow_basket(multi_item, multi_video_nodes, 2)

        self.assertEqual(len(multi_item.products), 2)
        self.assertEqual(multi_item.products[0].product_name, "Koko Sultan Jaguar Kemeja Koko")
        self.assertEqual(multi_item.products[1].product_name, "Celana Chino Panjang Pria Slimfit")
        # Verify Share (484, 506) and Copy Link (92, 1445) were NEVER tapped on the multi-product sheet
        self.assertFalse(any("tap 484 506" in c for c in tapped_cmds))
        self.assertFalse(any("tap 92 1445" in c for c in tapped_cmds))

        # 5. _get_current_focus_window must NEVER return 'mCurrentFocus=null' during window animations
        def fake_null_focus_runner(cmd, timeout=10):
            return AdbCommandResult(
                0,
                "  mCurrentFocus=null\n  mFocusedApp=ActivityRecord{7262107 u0 com.ss.android.ugc.trill/com.ss.android.ugc.profile.platform.framework.aweme.profile.ui.UserProfileHostActivity t512}\n",
                "",
            )

        null_focus_driver = AdbUiDriver(
            "192.168.1.12:33193", adb_path="/usr/bin/adb", runner=fake_null_focus_runner
        )
        focus_val = null_focus_driver._get_current_focus_window()
        self.assertNotIn("mCurrentFocus=null", focus_val)
        self.assertIn("UserProfileHostActivity", focus_val)

        # 6. _row_sigs_match, _is_row_visited & _select_unvisited_rows_below_visited must handle
        # real Job #23 view increments (+7-9%) and enforce the Geometric Top-to-Bottom Invariant
        from shadowkreator.core.ui_automation import (
            _is_row_visited,
            _row_sigs_match,
            _select_unvisited_rows_below_visited,
        )

        row0_before = ("1,128", "516", "467")
        row0_after = ("1,198", "555", "502")
        row1_before = ("514", "414", "377")
        row1_after = ("559", "442", "406")
        row2_visited = ("296", "361", "463")
        row3_unvisited = ("28.6K", "608", "631")

        self.assertTrue(_row_sigs_match(row0_before, row0_after))
        self.assertTrue(_row_sigs_match(row1_before, row1_after))
        self.assertFalse(_row_sigs_match(row0_before, row1_before))
        self.assertTrue(_is_row_visited(row0_after, {row0_before}))

        visited = {row0_before, row1_before, row2_visited}
        recovered_screen_rows = [
            (row0_after, [(120, 500, "1,198"), (360, 500, "555"), (600, 500, "502")]),
            (row1_after, [(120, 840, "559"), (360, 840, "442"), (600, 840, "406")]),
            (row2_visited, [(120, 1180, "296"), (360, 1180, "361"), (600, 1180, "463")]),
            (row3_unvisited, [(120, 1450, "28.6K"), (360, 1450, "608"), (600, 1450, "631")]),
        ]
        unvisited = _select_unvisited_rows_below_visited(recovered_screen_rows, visited)
        self.assertEqual(len(unvisited), 1)
        self.assertEqual(unvisited[0][0], row3_unvisited)


if __name__ == "__main__":
    unittest.main()
