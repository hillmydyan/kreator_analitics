"""
Scraping Controller (PRD Section 2.4 #3, Section 5, Section 7 & Section 8).

Orchestrates the end-to-end scraping workflow, real progress tracking,
incremental database persistence, error isolation, and job resume after interruption.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, List, Optional, Set

from shadowkreator.core.database import DatabaseManager, normalize_video_url, utc_now_iso
from shadowkreator.core.device_manager import DeviceManager
from shadowkreator.core.extractor import is_within_period, parse_upload_date, parse_views_count
from shadowkreator.core.models import (
    DeviceInfo,
    ExtractionStatus,
    JobStatus,
    LogLevel,
    ScrapingJobRecord,
    VideoProductRecord,
    VideoRecord,
)
from shadowkreator.core.ui_automation import RawVideoItem, UiAutomationDriver


@dataclass
class ScrapingProgressSnapshot:
    """Real-time progress state emitted to Scraping Monitor Page (PRD Section 4.4)."""

    job_id: int
    creator_username: str
    period_days: int
    job_status: str
    total_videos: int
    processed_videos: int
    failed_videos: int
    total_products: int
    latest_log_message: str

    @property
    def progress_percent(self) -> int:
        """
        Actual completion percentage based on processed + failed videos over total videos
        (PRD Section 4.4: must be based on actual completed work, not fake animation).
        """
        if self.job_status in (
            JobStatus.COMPLETED.value,
            JobStatus.COMPLETED_WITH_WARNINGS.value,
        ):
            return 100
        if self.total_videos <= 0:
            return 0
        done = min(self.processed_videos + self.failed_videos, self.total_videos)
        return int((done * 100) // self.total_videos)


ProgressCallback = Callable[[ScrapingProgressSnapshot], None]


class ScrapingController:
    """Controls creator scraping lifecycle, validation, incremental saving, and resume."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self._active_job_id: Optional[int] = None
        self._cancel_requested: bool = False

    @property
    def is_busy(self) -> bool:
        return self._active_job_id is not None

    def request_cancel(self) -> None:
        """Signal the currently running scraping job to stop cleanly."""
        self._cancel_requested = True

    def run_scraping_job(
        self,
        *,
        creator_username: str,
        period_days: int,
        device: Optional[DeviceInfo],
        ui_driver: UiAutomationDriver,
        compliance_accepted: bool = True,
        reference_date: Optional[date] = None,
        on_progress: Optional[ProgressCallback] = None,
        resume_job_id: Optional[int] = None,
    ) -> ScrapingJobRecord:
        """
        Execute or resume a creator scraping job (PRD Section 5 & Section 7).
        """
        if self.is_busy:
            raise RuntimeError(
                "Sistem sedang menjalankan pekerjaan scraping lain. Tunggu hingga selesai."
            )

        if not compliance_accepted:
            raise RuntimeError(
                "Anda harus menyetujui pernyataan otorisasi akses sebelum memulai ekstraksi."
            )

        ok_dev, dev_msg = DeviceManager.validate_device_for_scraping(device)
        if not ok_dev:
            raise RuntimeError(dev_msg)

        clean_username = creator_username.strip()
        if not clean_username:
            raise ValueError("Username kreator tidak boleh kosong.")
        if not clean_username.startswith("@"):
            clean_username = f"@{clean_username}"

        if period_days not in (7, 30):
            raise ValueError("Periode analisis hanya mendukung 7 atau 30 hari.")

        ref_dt = reference_date or date.today()
        start_dt = ref_dt - timedelta(days=period_days)

        if resume_job_id is not None:
            job = self.db.get_job(resume_job_id)
            if job is None:
                raise ValueError(f"Job #{resume_job_id} tidak ditemukan untuk di-resume.")
            self.db.update_job_progress(
                job.id,
                job_status=JobStatus.INITIALIZING.value,
                error_message=None,
            )
            job.job_status = JobStatus.INITIALIZING.value
        else:
            job = self.db.create_job(
                ScrapingJobRecord(
                    creator_username=clean_username,
                    period_days=period_days,
                    start_date=start_dt.isoformat(),
                    end_date=ref_dt.isoformat(),
                    started_at=utc_now_iso(),
                    job_status=JobStatus.INITIALIZING.value,
                )
            )

        self._active_job_id = job.id
        self._cancel_requested = False

        def emit(status: str, message: str, level: str = LogLevel.INFO.value, video_id: Optional[str] = None) -> None:
            self.db.add_log(job.id, level, status, message, video_id=video_id)
            current = self.db.get_job(job.id)
            if on_progress and current:
                on_progress(
                    ScrapingProgressSnapshot(
                        job_id=current.id,
                        creator_username=current.creator_username,
                        period_days=current.period_days,
                        job_status=current.job_status,
                        total_videos=current.total_videos,
                        processed_videos=current.processed_videos,
                        failed_videos=current.failed_videos,
                        total_products=current.total_products,
                        latest_log_message=message,
                    )
                )

        try:
            emit(
                JobStatus.INITIALIZING.value,
                f"Memulai pekerjaan untuk {clean_username} (periode {period_days} hari) menggunakan {ui_driver.driver_name}.",
            )

            # Stage 2 PoC screen validation
            poc = ui_driver.verify_screen_access()
            if not poc.success:
                self.db.update_job_progress(
                    job.id,
                    job_status=JobStatus.FAILED.value,
                    completed_at=utc_now_iso(),
                    error_message=poc.message,
                )
                emit(JobStatus.FAILED.value, poc.message, LogLevel.ERROR.value)
                return self.db.get_job(job.id)

            # 5.2 Video Discovery & Incremental Live Persistence
            self.db.update_job_progress(job.id, job_status=JobStatus.SCANNING_VIDEOS.value)
            emit(JobStatus.SCANNING_VIDEOS.value, f"Membuka profil {clean_username} dan memindai daftar video...")

            existing_videos = self.db.list_videos(job.id)
            already_processed_keys: Set[str] = {
                (v.video_id or normalize_video_url(v.video_url)) for v in existing_videos
            }
            seen_keys_in_discovery: Set[str] = set(already_processed_keys)
            processed_count = len(existing_videos)
            failed_count = 0
            warnings_count = 0
            product_links_count = sum(v.product_count for v in existing_videos)

            def _persist_single_video_item(item: RawVideoItem) -> None:
                nonlocal processed_count, failed_count, warnings_count, product_links_count

                parsed_dt = parse_upload_date(item.raw_upload_date, reference_date=ref_dt)
                if parsed_dt is not None:
                    in_window, _, _ = is_within_period(parsed_dt, period_days, reference_date=ref_dt)
                    if not in_window:
                        emit(
                            JobStatus.SCANNING_VIDEOS.value,
                            f"Melewati video {item.video_id or item.video_url} (tanggal upload {parsed_dt.isoformat()} di luar periode {period_days} hari).",
                            LogLevel.INFO.value,
                            video_id=item.video_id,
                        )
                        return
                else:
                    warnings_count += 1
                    emit(
                        JobStatus.SCANNING_VIDEOS.value,
                        f"Tanggal upload tidak terbaca untuk video {item.video_id or item.video_url}; ditandai tidak tersedia.",
                        LogLevel.WARNING.value,
                        video_id=item.video_id,
                    )

                dedup_key = (item.video_id or normalize_video_url(item.video_url)).strip()
                if dedup_key and dedup_key in already_processed_keys:
                    return
                if dedup_key and dedup_key in seen_keys_in_discovery:
                    emit(
                        JobStatus.SCANNING_VIDEOS.value,
                        f"Mencegah duplikasi video {dedup_key} dalam job yang sama.",
                        LogLevel.WARNING.value,
                        video_id=item.video_id,
                    )
                    return
                if dedup_key:
                    seen_keys_in_discovery.add(dedup_key)
                    already_processed_keys.add(dedup_key)

                if item.error_message:
                    failed_count += 1
                    warnings_count += 1
                    self.db.update_job_progress(job.id, failed_videos=failed_count)
                    emit(
                        JobStatus.EXTRACTING_VIDEO_DATA.value,
                        f"Gagal memproses video {dedup_key}: {item.error_message}",
                        LogLevel.ERROR.value,
                        video_id=item.video_id,
                    )
                    return

                parsed_views = parse_views_count(item.raw_views)
                if parsed_views is None:
                    warnings_count += 1
                    emit(
                        JobStatus.EXTRACTING_VIDEO_DATA.value,
                        f"Views tidak terbaca pada video {dedup_key}; disimpan sebagai NULL tanpa estimasi.",
                        LogLevel.WARNING.value,
                        video_id=item.video_id,
                    )

                valid_products = [
                    p for p in item.products if p.product_name and p.product_name.strip()
                ]
                if len(valid_products) < len(item.products):
                    warnings_count += 1
                    emit(
                        JobStatus.EXTRACTING_PRODUCTS.value,
                        f"Ditemukan entri produk dengan nama kosong pada video {dedup_key}; dilewati.",
                        LogLevel.WARNING.value,
                        video_id=item.video_id,
                    )

                saved_video = self.db.save_video(
                    VideoRecord(
                        job_id=job.id,
                        creator_username=clean_username,
                        video_id=item.video_id,
                        video_url=item.video_url,
                        upload_date=parsed_dt.isoformat() if parsed_dt else None,
                        views=parsed_views,
                        has_product=bool(item.has_yellow_basket and valid_products),
                        product_count=len(valid_products),
                        scraped_at=utc_now_iso(),
                    )
                )

                if item.has_yellow_basket and valid_products:
                    self.db.update_job_progress(job.id, job_status=JobStatus.EXTRACTING_PRODUCTS.value)
                    for raw_prod in valid_products:
                        prod_record = self.db.resolve_or_create_product(
                            raw_prod.product_name, raw_prod.product_url
                        )
                        if prod_record.is_ambiguous:
                            warnings_count += 1
                            status = ExtractionStatus.AMBIGUOUS.value
                            emit(
                                JobStatus.EXTRACTING_PRODUCTS.value,
                                f"Produk '{prod_record.product_name}' ditandai ambigu karena nama sama tanpa link pembeda.",
                                LogLevel.WARNING.value,
                                video_id=item.video_id,
                            )
                        elif not raw_prod.product_url:
                            warnings_count += 1
                            status = ExtractionStatus.LINK_UNAVAILABLE.value
                            emit(
                                JobStatus.EXTRACTING_PRODUCTS.value,
                                f"Produk '{prod_record.product_name}' disimpan tanpa link (Link tidak tersedia di UI).",
                                LogLevel.WARNING.value,
                                video_id=item.video_id,
                            )
                        else:
                            status = ExtractionStatus.SUCCESS.value

                        self.db.link_video_product(
                            VideoProductRecord(
                                job_id=job.id,
                                video_id=saved_video.id,
                                product_id=prod_record.id,
                                views_snapshot=parsed_views,
                                extraction_status=status,
                                scraped_at=utc_now_iso(),
                            )
                        )
                        product_links_count += 1

                processed_count += 1
                self.db.update_job_progress(
                    job.id,
                    job_status=JobStatus.EXTRACTING_VIDEO_DATA.value,
                    total_videos=max(processed_count + failed_count, processed_count),
                    processed_videos=processed_count,
                    total_products=product_links_count,
                )
                emit(
                    JobStatus.EXTRACTING_VIDEO_DATA.value,
                    f"Video {saved_video.video_id or saved_video.video_url} tersimpan ({len(valid_products)} produk).",
                    LogLevel.INFO.value,
                    video_id=saved_video.video_id,
                )

            if hasattr(ui_driver, "set_progress_hooks"):
                ui_driver.set_progress_hooks(
                    on_status=lambda msg: emit(JobStatus.SCANNING_VIDEOS.value, msg),
                    should_cancel=lambda: self._cancel_requested,
                    on_video_collected=_persist_single_video_item,
                )

            raw_items: List[RawVideoItem] = ui_driver.collect_creator_videos(
                clean_username, period_days, reference_date=ref_dt
            )

            # Persist any items returned in batch (e.g. MockUiDriver or items not yet saved via hook)
            for item in raw_items:
                if self._cancel_requested:
                    break
                _persist_single_video_item(item)

            total_eligible = max(processed_count + failed_count, len(self.db.list_videos(job.id)))
            self.db.update_job_progress(
                job.id,
                total_videos=total_eligible,
                processed_videos=processed_count,
                total_products=product_links_count,
            )

            # 5.5 Final Processing (including when user clicked 'Stop & Selesaikan')
            self.db.update_job_progress(job.id, job_status=JobStatus.PROCESSING_DATA.value)
            emit(JobStatus.PROCESSING_DATA.value, "Menghitung agregasi produk dan menyusun laporan analitik...")

            final_status = (
                JobStatus.COMPLETED_WITH_WARNINGS.value
                if (warnings_count > 0 or failed_count > 0 or self._cancel_requested)
                else JobStatus.COMPLETED.value
            )
            stop_note = " (Dihentikan & diselesaikan oleh pengguna)" if self._cancel_requested else ""
            self.db.update_job_progress(
                job.id,
                job_status=final_status,
                completed_at=utc_now_iso(),
            )
            emit(
                final_status,
                f"Selesai{stop_note}: {processed_count} video berhasil disimpan, {product_links_count} relasi produk tercatat.",
            )
            return self.db.get_job(job.id)

        except ConnectionError as exc:
            # Device disconnected mid-job (PRD Section 7)
            self.db.update_job_progress(
                job.id,
                job_status=JobStatus.INTERRUPTED.value,
                completed_at=utc_now_iso(),
                error_message=str(exc),
            )
            emit(
                JobStatus.INTERRUPTED.value,
                f"Koneksi perangkat terputus: {exc}. Data yang sudah diperoleh telah disimpan dan job dapat di-resume.",
                LogLevel.ERROR.value,
            )
            return self.db.get_job(job.id)
        except Exception as exc:
            self.db.update_job_progress(
                job.id,
                job_status=JobStatus.FAILED.value,
                completed_at=utc_now_iso(),
                error_message=str(exc),
            )
            emit(JobStatus.FAILED.value, f"Pekerjaan gagal: {exc}", LogLevel.ERROR.value)
            return self.db.get_job(job.id)
        finally:
            self._active_job_id = None
            self._cancel_requested = False
