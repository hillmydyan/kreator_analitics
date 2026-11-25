"""CLI & Desktop GUI entry point for ShadowKreator."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from shadowkreator.config import DEFAULT_DB_PATH, DEFAULT_EXPORT_DIR
from shadowkreator.core.analytics import AnalyticsEngine
from shadowkreator.core.controller import ScrapingController
from shadowkreator.core.database import DatabaseManager, utc_now_iso
from shadowkreator.core.exporter import ExportManager, build_export_filename
from shadowkreator.core.models import ConnectionType, DeviceInfo, DeviceStatus
from shadowkreator.core.ui_automation import MockUiDriver


def run_cli_demo() -> int:
    """Run an end-to-end simulated scraping, analytics, and Excel export in terminal."""
    db = DatabaseManager(DEFAULT_DB_PATH)
    controller = ScrapingController(db)
    analytics = AnalyticsEngine(db)
    exporter = ExportManager(db)

    sim_device = DeviceInfo(
        device_serial="SIMULATOR-ANDROID-13",
        device_name="Samsung Galaxy A52 (Simulator)",
        model="SM-A525F",
        android_version="13",
        connection_type=ConnectionType.USB.value,
        connection_status=DeviceStatus.CONNECTED.value,
        sdk_int=33,
        last_connected_at=utc_now_iso(),
    )
    db.upsert_device(sim_device)
    driver = MockUiDriver(sim_device.device_serial)

    print("=== Tahap 2 PoC Screen Verification ===")
    poc = driver.verify_screen_access()
    print(f"Status PoC: {poc.message} (UI Nodes: {poc.ui_node_count})")

    print("\n=== Menjalankan Scraping Kreator (@creator_affiliate, 30 Hari) ===")
    job = controller.run_scraping_job(
        creator_username="@creator_affiliate",
        period_days=30,
        device=sim_device,
        ui_driver=driver,
        compliance_accepted=True,
        on_progress=lambda snap: print(
            f"  [{snap.progress_percent:3d}%] {snap.job_status}: {snap.latest_log_message}"
        ),
    )

    report = analytics.build_job_report(job.id)
    print("\n=== Ringkasan Analytics Dashboard ===")
    print(f"Kreator          : {report.creator_username}")
    print(f"Periode          : {report.period_days} Hari")
    print(f"Total Video      : {report.total_videos}")
    print(f"Total Views      : {report.total_views:,}")
    print(f"Produk Unik      : {report.total_unique_products}")
    print("\nTop Produk:")
    for idx, prod in enumerate(report.products, start=1):
        print(
            f"  {idx}. {prod.product_name} | Video: {prod.video_count} | "
            f"Total Views: {prod.total_views:,} | Avg: {prod.average_views:,.0f} | "
            f"Kontribusi: {prod.view_contribution_pct:.2f}%"
        )

    DEFAULT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DEFAULT_EXPORT_DIR / build_export_filename(
        report.creator_username, report.period_days, date.today()
    )
    saved = exporter.export_job_to_excel(job.id, out_path)
    print(f"\n=== Export Excel (4 Sheet) Berhasil ===\nFile: {saved}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="TikTok Creator Product Analytics Scraper")
    parser.add_argument(
        "--cli-demo",
        action="store_true",
        help="Jalankan simulasi end-to-end (PoC, Scraping, Analytics, & Export Excel) lewat terminal.",
    )
    args = parser.parse_args()

    if args.cli_demo:
        return run_cli_demo()

    from shadowkreator.ui.main_window import run_desktop_app

    return run_desktop_app()


if __name__ == "__main__":
    sys.exit(main())
