"""SQLite Database Manager implementing PRD Section 6 schema and persistence rules."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Union
from urllib.parse import urlparse, urlunparse

from shadowkreator.config import DEFAULT_DB_PATH
from shadowkreator.core.models import (
    DeviceInfo,
    ProductRecord,
    ScrapingJobRecord,
    ScrapingLogRecord,
    VideoProductRecord,
    VideoRecord,
)


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_video_url(url: str) -> str:
    """Normalize a TikTok video URL by stripping query/fragment and trailing slash (PRD 6.3)."""
    if not url:
        return ""
    raw = url.strip()
    parsed = urlparse(raw)
    if not parsed.scheme:
        return raw.rstrip("/")
    clean_path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), clean_path, "", "", ""))


def normalize_product_name(name: str) -> str:
    """Normalize product name for grouping comparisons (PRD 3.5)."""
    if not name:
        return ""
    return " ".join(name.strip().lower().split())


class DatabaseManager:
    """Manages local SQLite storage for devices, jobs, videos, products, and logs."""

    def __init__(self, db_path: Union[str, Path] = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._memory_conn: Optional[sqlite3.Connection] = None
        if str(self.db_path) == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory_conn.row_factory = sqlite3.Row
            self._memory_conn.execute("PRAGMA foreign_keys = ON;")
        self.initialize_schema()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Provide a transactional SQLite connection with foreign keys enabled."""
        if self._memory_conn is not None:
            try:
                yield self._memory_conn
                self._memory_conn.commit()
            except Exception:
                self._memory_conn.rollback()
                raise
            return

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize_schema(self) -> None:
        """Create all 6 PRD tables and indexes if they do not exist (PRD Section 6)."""
        schema_sql = """
        -- 6.1 Table: devices
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_serial TEXT NOT NULL UNIQUE,
            device_name TEXT NOT NULL,
            model TEXT NOT NULL,
            android_version TEXT NOT NULL,
            connection_type TEXT NOT NULL,
            connection_status TEXT NOT NULL,
            last_connected_at TEXT
        );

        -- 6.2 Table: scraping_jobs
        CREATE TABLE IF NOT EXISTS scraping_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_username TEXT NOT NULL,
            period_days INTEGER NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            total_videos INTEGER NOT NULL DEFAULT 0,
            processed_videos INTEGER NOT NULL DEFAULT 0,
            failed_videos INTEGER NOT NULL DEFAULT 0,
            total_products INTEGER NOT NULL DEFAULT 0,
            job_status TEXT NOT NULL,
            error_message TEXT
        );

        -- 6.3 Table: videos
        -- Scoped with job_id so historical scraping jobs are never overwritten (PRD 4.8),
        -- and deduplicated within a job by unique_key (video_id if present, else normalized video_url).
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            video_id TEXT,
            unique_key TEXT NOT NULL,
            creator_username TEXT NOT NULL,
            video_url TEXT NOT NULL,
            upload_date TEXT,
            views INTEGER,
            has_product INTEGER NOT NULL DEFAULT 0,
            product_count INTEGER NOT NULL DEFAULT 0,
            scraped_at TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES scraping_jobs(id) ON DELETE CASCADE,
            UNIQUE(job_id, unique_key)
        );

        -- 6.4 Table: products
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            product_url TEXT,
            is_ambiguous INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        -- 6.5 Table: video_products
        CREATE TABLE IF NOT EXISTS video_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            video_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            views_snapshot INTEGER,
            extraction_status TEXT NOT NULL,
            scraped_at TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES scraping_jobs(id) ON DELETE CASCADE,
            FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        );

        -- 6.6 Table: scraping_logs
        CREATE TABLE IF NOT EXISTS scraping_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            video_id TEXT,
            log_level TEXT NOT NULL,
            action TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES scraping_jobs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_videos_job_id ON videos(job_id);
        CREATE INDEX IF NOT EXISTS idx_videos_creator ON videos(creator_username);
        CREATE INDEX IF NOT EXISTS idx_products_norm_name ON products(normalized_name);
        CREATE INDEX IF NOT EXISTS idx_video_products_video ON video_products(video_id);
        CREATE INDEX IF NOT EXISTS idx_video_products_product ON video_products(product_id);
        CREATE INDEX IF NOT EXISTS idx_logs_job_id ON scraping_logs(job_id);
        """
        with self.connection() as conn:
            conn.executescript(schema_sql)

    # ------------------------------------------------------------------
    # 6.1 Devices Repository
    # ------------------------------------------------------------------
    def upsert_device(self, device: DeviceInfo) -> DeviceInfo:
        """Insert or update an Android device record by device_serial."""
        now = device.last_connected_at or utc_now_iso()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO devices (
                    device_serial, device_name, model, android_version,
                    connection_type, connection_status, last_connected_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_serial) DO UPDATE SET
                    device_name = excluded.device_name,
                    model = excluded.model,
                    android_version = excluded.android_version,
                    connection_type = excluded.connection_type,
                    connection_status = excluded.connection_status,
                    last_connected_at = excluded.last_connected_at
                """,
                (
                    device.device_serial,
                    device.device_name,
                    device.model,
                    device.android_version,
                    device.connection_type,
                    device.connection_status,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM devices WHERE device_serial = ?",
                (device.device_serial,),
            ).fetchone()
            device.id = row["id"]
            device.last_connected_at = row["last_connected_at"]
            return device

    def list_devices(self) -> List[DeviceInfo]:
        """Return all stored devices ordered by most recently connected."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM devices ORDER BY last_connected_at DESC, id DESC"
            ).fetchall()
        return [
            DeviceInfo(
                id=row["id"],
                device_serial=row["device_serial"],
                device_name=row["device_name"],
                model=row["model"],
                android_version=row["android_version"],
                connection_type=row["connection_type"],
                connection_status=row["connection_status"],
                last_connected_at=row["last_connected_at"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # 6.2 Scraping Jobs Repository
    # ------------------------------------------------------------------
    def create_job(self, job: ScrapingJobRecord) -> ScrapingJobRecord:
        """Insert a new scraping job without overwriting historical jobs."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scraping_jobs (
                    creator_username, period_days, start_date, end_date,
                    started_at, completed_at, total_videos, processed_videos,
                    failed_videos, total_products, job_status, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.creator_username,
                    job.period_days,
                    job.start_date,
                    job.end_date,
                    job.started_at,
                    job.completed_at,
                    job.total_videos,
                    job.processed_videos,
                    job.failed_videos,
                    job.total_products,
                    job.job_status,
                    job.error_message,
                ),
            )
            job.id = cursor.lastrowid
            return job

    def update_job_progress(
        self,
        job_id: int,
        *,
        job_status: Optional[str] = None,
        total_videos: Optional[int] = None,
        processed_videos: Optional[int] = None,
        failed_videos: Optional[int] = None,
        total_products: Optional[int] = None,
        completed_at: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Incremental update of scraping job state (PRD Section 8.1)."""
        fields = []
        params = []
        if job_status is not None:
            fields.append("job_status = ?")
            params.append(job_status)
        if total_videos is not None:
            fields.append("total_videos = ?")
            params.append(total_videos)
        if processed_videos is not None:
            fields.append("processed_videos = ?")
            params.append(processed_videos)
        if failed_videos is not None:
            fields.append("failed_videos = ?")
            params.append(failed_videos)
        if total_products is not None:
            fields.append("total_products = ?")
            params.append(total_products)
        if completed_at is not None:
            fields.append("completed_at = ?")
            params.append(completed_at)
        if error_message is not None:
            fields.append("error_message = ?")
            params.append(error_message)

        if not fields:
            return

        params.append(job_id)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE scraping_jobs SET {', '.join(fields)} WHERE id = ?",
                tuple(params),
            )

    def get_job(self, job_id: int) -> Optional[ScrapingJobRecord]:
        """Fetch a scraping job by ID."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM scraping_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if not row:
            return None
        return ScrapingJobRecord(
            id=row["id"],
            creator_username=row["creator_username"],
            period_days=row["period_days"],
            start_date=row["start_date"],
            end_date=row["end_date"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            total_videos=row["total_videos"],
            processed_videos=row["processed_videos"],
            failed_videos=row["failed_videos"],
            total_products=row["total_products"],
            job_status=row["job_status"],
            error_message=row["error_message"],
        )

    def list_jobs(self) -> List[ScrapingJobRecord]:
        """List all scraping jobs ordered newest first (PRD Section 4.8)."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM scraping_jobs ORDER BY id DESC"
            ).fetchall()
        return [
            ScrapingJobRecord(
                id=row["id"],
                creator_username=row["creator_username"],
                period_days=row["period_days"],
                start_date=row["start_date"],
                end_date=row["end_date"],
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                total_videos=row["total_videos"],
                processed_videos=row["processed_videos"],
                failed_videos=row["failed_videos"],
                total_products=row["total_products"],
                job_status=row["job_status"],
                error_message=row["error_message"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # 6.3 Videos Repository (with deduplication per PRD 3.2 & 6.3)
    # ------------------------------------------------------------------
    def save_video(self, video: VideoRecord) -> VideoRecord:
        """Save or update a video within a job, preventing duplicates by video_id or normalized URL."""
        norm_url = normalize_video_url(video.video_url)
        unique_key = (video.video_id.strip() if video.video_id else "") or norm_url
        if not unique_key:
            raise ValueError("Video must have either video_id or video_url")

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO videos (
                    job_id, video_id, unique_key, creator_username, video_url,
                    upload_date, views, has_product, product_count, scraped_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, unique_key) DO UPDATE SET
                    video_url = excluded.video_url,
                    upload_date = COALESCE(excluded.upload_date, videos.upload_date),
                    views = COALESCE(excluded.views, videos.views),
                    has_product = excluded.has_product,
                    product_count = excluded.product_count,
                    scraped_at = excluded.scraped_at
                """,
                (
                    video.job_id,
                    video.video_id,
                    unique_key,
                    video.creator_username,
                    norm_url or video.video_url,
                    video.upload_date,
                    video.views,
                    1 if video.has_product else 0,
                    video.product_count,
                    video.scraped_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM videos WHERE job_id IS ? AND unique_key = ?",
                (video.job_id, unique_key),
            ).fetchone()
            video.id = row["id"]
            return video

    def list_videos(self, job_id: Optional[int] = None) -> List[VideoRecord]:
        """List videos, optionally filtered by job_id."""
        with self.connection() as conn:
            if job_id is not None:
                rows = conn.execute(
                    "SELECT * FROM videos WHERE job_id = ? ORDER BY id ASC",
                    (job_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM videos ORDER BY id DESC").fetchall()
        return [
            VideoRecord(
                id=row["id"],
                job_id=row["job_id"],
                video_id=row["video_id"],
                creator_username=row["creator_username"],
                video_url=row["video_url"],
                upload_date=row["upload_date"],
                views=row["views"],
                has_product=bool(row["has_product"]),
                product_count=row["product_count"],
                scraped_at=row["scraped_at"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # 6.4 & 6.5 Products & VideoProducts Repository (PRD 3.5 grouping rules)
    # ------------------------------------------------------------------
    def resolve_or_create_product(
        self, product_name: str, product_url: Optional[str] = None
    ) -> ProductRecord:
        """
        Resolve or create a product following PRD Section 3.5 grouping rules:
        - Products with similar/same name but DIFFERENT URLs must NOT be merged.
        - Products with same normalized name where one lacks a distinguishing URL
          are flagged as is_ambiguous=True for inspection.
        """
        clean_name = product_name.strip()
        if not clean_name:
            raise ValueError("product_name cannot be empty (PRD Section 7.3)")

        norm_name = normalize_product_name(clean_name)
        clean_url = normalize_video_url(product_url) if product_url else None
        now = utc_now_iso()

        with self.connection() as conn:
            candidates = conn.execute(
                "SELECT * FROM products WHERE normalized_name = ? ORDER BY id ASC",
                (norm_name,),
            ).fetchall()

            # Case 1: Both have URLs and they match -> exact same product
            if clean_url:
                for row in candidates:
                    if row["product_url"] and row["product_url"] == clean_url:
                        conn.execute(
                            "UPDATE products SET updated_at = ? WHERE id = ?",
                            (now, row["id"]),
                        )
                        return ProductRecord(
                            id=row["id"],
                            product_name=row["product_name"],
                            normalized_name=row["normalized_name"],
                            product_url=row["product_url"],
                            is_ambiguous=bool(row["is_ambiguous"]),
                            created_at=row["created_at"],
                            updated_at=now,
                        )

                # If candidates exist with the same name but NO URL (or different URL):
                # Do NOT merge automatically if URL differs; if existing had no URL, mark ambiguous.
                for row in candidates:
                    if not row["product_url"]:
                        conn.execute(
                            "UPDATE products SET is_ambiguous = 1, updated_at = ? WHERE id = ?",
                            (now, row["id"]),
                        )

                cursor = conn.execute(
                    """
                    INSERT INTO products (
                        product_name, normalized_name, product_url,
                        is_ambiguous, created_at, updated_at
                    )
                    VALUES (?, ?, ?, 0, ?, ?)
                    """,
                    (clean_name, norm_name, clean_url, now, now),
                )
                return ProductRecord(
                    id=cursor.lastrowid,
                    product_name=clean_name,
                    normalized_name=norm_name,
                    product_url=clean_url,
                    is_ambiguous=False,
                    created_at=now,
                    updated_at=now,
                )

            # Case 2: Incoming product has NO URL
            if candidates:
                # If any existing candidate has a URL, identity is ambiguous without a distinguishing URL
                has_url_candidate = any(bool(r["product_url"]) for r in candidates)
                no_url_candidate = next((r for r in candidates if not r["product_url"]), None)
                if no_url_candidate:
                    is_ambig = bool(no_url_candidate["is_ambiguous"]) or has_url_candidate
                    conn.execute(
                        "UPDATE products SET is_ambiguous = ?, updated_at = ? WHERE id = ?",
                        (1 if is_ambig else 0, now, no_url_candidate["id"]),
                    )
                    return ProductRecord(
                        id=no_url_candidate["id"],
                        product_name=no_url_candidate["product_name"],
                        normalized_name=no_url_candidate["normalized_name"],
                        product_url=None,
                        is_ambiguous=is_ambig,
                        created_at=no_url_candidate["created_at"],
                        updated_at=now,
                    )

                # Only URL-bearing candidates exist -> create separate ambiguous entry (do not auto-merge)
                cursor = conn.execute(
                    """
                    INSERT INTO products (
                        product_name, normalized_name, product_url,
                        is_ambiguous, created_at, updated_at
                    )
                    VALUES (?, ?, NULL, 1, ?, ?)
                    """,
                    (clean_name, norm_name, now, now),
                )
                return ProductRecord(
                    id=cursor.lastrowid,
                    product_name=clean_name,
                    normalized_name=norm_name,
                    product_url=None,
                    is_ambiguous=True,
                    created_at=now,
                    updated_at=now,
                )

            # First time seeing this product name (and no URL yet)
            cursor = conn.execute(
                """
                INSERT INTO products (
                    product_name, normalized_name, product_url,
                    is_ambiguous, created_at, updated_at
                )
                VALUES (?, ?, NULL, 0, ?, ?)
                """,
                (clean_name, norm_name, now, now),
            )
            return ProductRecord(
                id=cursor.lastrowid,
                product_name=clean_name,
                normalized_name=norm_name,
                product_url=None,
                is_ambiguous=False,
                created_at=now,
                updated_at=now,
            )

    def link_video_product(self, vp: VideoProductRecord) -> VideoProductRecord:
        """Create a relation between a video and a product (PRD Section 6.5)."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO video_products (
                    job_id, video_id, product_id, views_snapshot,
                    extraction_status, scraped_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    vp.job_id,
                    vp.video_id,
                    vp.product_id,
                    vp.views_snapshot,
                    vp.extraction_status,
                    vp.scraped_at,
                ),
            )
            vp.id = cursor.lastrowid
            return vp

    # ------------------------------------------------------------------
    # 6.6 Scraping Logs Repository
    # ------------------------------------------------------------------
    def add_log(
        self,
        job_id: int,
        log_level: str,
        action: str,
        message: str,
        video_id: Optional[str] = None,
    ) -> ScrapingLogRecord:
        """Record an activity or error log entry (PRD Section 6.6 & 7)."""
        now = utc_now_iso()
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scraping_logs (
                    job_id, video_id, log_level, action, message, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, video_id, log_level, action, message, now),
            )
            return ScrapingLogRecord(
                id=cursor.lastrowid,
                job_id=job_id,
                video_id=video_id,
                log_level=log_level,
                action=action,
                message=message,
                created_at=now,
            )

    def list_logs(self, job_id: int) -> List[ScrapingLogRecord]:
        """Return all logs for a specific job in chronological order."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM scraping_logs WHERE job_id = ? ORDER BY id ASC",
                (job_id,),
            ).fetchall()
        return [
            ScrapingLogRecord(
                id=row["id"],
                job_id=row["job_id"],
                video_id=row["video_id"],
                log_level=row["log_level"],
                action=row["action"],
                message=row["message"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
