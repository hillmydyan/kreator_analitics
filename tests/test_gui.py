"""Automated GUI tests for MainWindow (streamlined 4-menu sidebar + sub-tabs) using offscreen Qt."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from shadowkreator.core.database import DatabaseManager
from shadowkreator.core.models import JobStatus
from shadowkreator.ui.main_window import MainWindow


class TestDesktopGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.db = DatabaseManager(":memory:")
        self.window = MainWindow(db=self.db)

    def test_streamlined_four_menu_sidebar_and_end_to_end_gui_flow(self) -> None:
        # 1. Verify streamlined 4-menu sidebar and 3 sub-tabs in Analytics hub
        self.assertEqual(self.window.sidebar.count(), 4)
        self.assertEqual(self.window.stack.count(), 4)
        self.assertEqual(self.window.analytics_tabs.count(), 3)

        # 2. Navigate to Perangkat Android page & activate Simulator device + PoC check
        self.window.navigate_to(MainWindow.PAGE_DEVICE)
        self.window._on_activate_simulator()
        self.assertIsNotNone(self.window.active_device)
        self.assertEqual(self.window.tbl_devices.rowCount(), 1)

        self.window._on_run_poc_check()
        self.assertIn("PoC Mode Simulasi siap", self.window.lbl_device_poc_status.text())

        # 3. Navigate to unified Scraping Kreator page & verify pre-flight labels and yellow-basket checkbox
        self.window.navigate_to(MainWindow.PAGE_NEW_SCRAPING)
        self.window.inp_creator_username.setText("@creator_affiliate")
        self.window.cmb_period.setCurrentIndex(1)  # 30 Days
        self.assertEqual(self.window.lbl_pre_username.text(), "@creator_affiliate")
        self.assertIn("30 Hari", self.window.lbl_pre_period.text())
        self.assertFalse(self.window.chk_open_yellow_basket.isChecked())
        self.window.chk_open_yellow_basket.setChecked(True)
        self.assertTrue(self.window.chk_open_yellow_basket.isChecked())
        self.window._on_reset_scraping_form()
        self.assertFalse(self.window.chk_open_yellow_basket.isChecked())
        self.window.inp_creator_username.setText("@creator_affiliate")
        self.window.cmb_period.setCurrentIndex(1)

        # 4. Run scraping via controller and refresh GUI views on the same unified page
        driver = self.window._get_ui_driver_for_active_device()
        job = self.window.controller.run_scraping_job(
            creator_username="@creator_affiliate",
            period_days=30,
            device=self.window.active_device,
            ui_driver=driver,
            compliance_accepted=True,
            on_progress=self.window._on_worker_progress,
        )
        self.window._on_worker_finished(job)
        self.assertEqual(job.job_status, JobStatus.COMPLETED_WITH_WARNINGS.value)

        # 5. Check Beranda (Main Dashboard) summary cards
        self.window.navigate_to(MainWindow.PAGE_DASHBOARD)
        self.assertEqual(self.window.lbl_dash_jobs.text(), "1")
        self.assertEqual(self.window.lbl_dash_videos.text(), "4")
        self.assertEqual(self.window.lbl_dash_products.text(), "4")

        # 6. Check Analitik & Laporan -> Performa Produk tab & drill down to Product Detail
        self.window.navigate_to(MainWindow.PAGE_ANALYTICS)
        self.assertEqual(self.window.tbl_analytics_products.rowCount(), 4)
        self.window._on_open_selected_product_detail()
        self.assertEqual(self.window.products_sub_stack.currentIndex(), 1)
        self.assertEqual(self.window.tbl_pd_videos.rowCount(), 2)

        # 7. Check Analitik & Laporan -> Daftar Video tab & filters
        self.window.navigate_to(MainWindow.PAGE_VIDEO_DB)
        self.assertEqual(self.window.analytics_tabs.currentIndex(), MainWindow.TAB_VIDEOS)
        self.assertEqual(self.window.tbl_video_db.rowCount(), 4)
        self.window.cmb_vid_filter.setCurrentIndex(2)  # Filter Video Tanpa Produk
        self.assertEqual(self.window.tbl_video_db.rowCount(), 1)

        # 8. Check Analitik & Laporan -> Riwayat & Log tab
        self.window.navigate_to(MainWindow.PAGE_HISTORY)
        self.assertEqual(self.window.analytics_tabs.currentIndex(), MainWindow.TAB_HISTORY)
        self.assertEqual(self.window.tbl_history.rowCount(), 1)
        self.window._on_history_selection_changed()
        self.assertIn("Selesai", self.window.txt_history_logs.toPlainText())


if __name__ == "__main__":
    unittest.main()
