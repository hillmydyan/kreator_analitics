"""
Analytics Engine (Tahap 5 — PRD Section 2.4 #6, 3.5, 3.6, 4.5 & 4.6).

Computes creator overview metrics, product performance rankings, and product-level
video breakdowns following the exact PRD Section 3.6 formulas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from shadowkreator.core.database import DatabaseManager
from shadowkreator.core.models import VideoRecord

CALCULATION_NOTE_ID = (
    "Catatan Perhitungan: Jika satu video menampilkan beberapa produk di keranjang kuning, "
    "views video tersebut dihitung penuh pada masing-masing produk. Oleh karena itu, akumulasi "
    "total views antar-produk dapat melebihi total views seluruh video kreator."
)


@dataclass
class ProductPerformance:
    """Aggregated analytics for a single grouped product (PRD Section 3.6, 4.5 & 4.6)."""

    product_id: int
    product_name: str
    product_url: Optional[str]
    is_ambiguous: bool
    video_count: int
    total_views: int
    average_views: float
    view_contribution_pct: float
    videos: List[VideoRecord] = field(default_factory=list)


@dataclass
class CreatorAnalyticsReport:
    """Complete analytics summary for a scraping job (PRD Section 3.6 & 4.5)."""

    job_id: int
    creator_username: str
    period_days: int
    total_videos: int
    total_views: int
    average_views_per_video: float
    total_unique_products: int
    products: List[ProductPerformance]
    calculation_note: str = CALCULATION_NOTE_ID


class AnalyticsEngine:
    """Computes creator and product performance analytics from SQLite records."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def build_job_report(
        self,
        job_id: int,
        sort_by: str = "total_views",
        descending: bool = True,
        search_query: str = "",
    ) -> CreatorAnalyticsReport:
        """
        Compute Creator Overview and Product Performance Table for `job_id`
        matching PRD Section 3.6 formulas:
        - video_count = COUNT(DISTINCT video_id)
        - total_views = SUM(views of distinct videos linked to product)
        - average_views = total_views / video_count
        - view_contribution_pct = (total_views / creator_total_views) * 100
        """
        job = self.db.get_job(job_id)
        if job is None:
            raise ValueError(f"Scraping job #{job_id} tidak ditemukan.")

        videos = self.db.list_videos(job_id)
        total_videos = len(videos)
        creator_total_views = sum((v.views or 0) for v in videos)
        avg_creator_views = (
            creator_total_views / total_videos if total_videos > 0 else 0.0
        )

        video_by_db_id = {v.id: v for v in videos if v.id is not None}

        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    p.id AS product_id,
                    p.product_name,
                    p.product_url,
                    p.is_ambiguous,
                    vp.video_id AS video_db_id
                FROM video_products vp
                JOIN products p ON p.id = vp.product_id
                WHERE vp.job_id = ?
                ORDER BY p.id ASC, vp.video_id ASC
                """,
                (job_id,),
            ).fetchall()

        # Group distinct videos per product
        grouped: dict[int, dict] = {}
        for row in rows:
            pid = row["product_id"]
            vid_db_id = row["video_db_id"]
            if pid not in grouped:
                grouped[pid] = {
                    "product_id": pid,
                    "product_name": row["product_name"],
                    "product_url": row["product_url"],
                    "is_ambiguous": bool(row["is_ambiguous"]),
                    "video_ids": set(),
                }
            grouped[pid]["video_ids"].add(vid_db_id)

        product_performances: List[ProductPerformance] = []
        query_norm = search_query.strip().lower()

        for pid, info in grouped.items():
            if query_norm and query_norm not in info["product_name"].lower():
                continue

            linked_videos = [
                video_by_db_id[vid_id]
                for vid_id in sorted(info["video_ids"])
                if vid_id in video_by_db_id
            ]
            v_count = len(linked_videos)
            p_total_views = sum((v.views or 0) for v in linked_videos)
            p_avg_views = (p_total_views / v_count) if v_count > 0 else 0.0
            contrib_pct = (
                (p_total_views / creator_total_views * 100.0)
                if creator_total_views > 0
                else 0.0
            )

            product_performances.append(
                ProductPerformance(
                    product_id=pid,
                    product_name=info["product_name"],
                    product_url=info["product_url"],
                    is_ambiguous=info["is_ambiguous"],
                    video_count=v_count,
                    total_views=p_total_views,
                    average_views=round(p_avg_views, 2),
                    view_contribution_pct=round(contrib_pct, 2),
                    videos=linked_videos,
                )
            )

        if sort_by == "video_count":
            product_performances.sort(
                key=lambda x: (x.video_count, x.total_views), reverse=descending
            )
        else:
            product_performances.sort(
                key=lambda x: (x.total_views, x.video_count), reverse=descending
            )

        return CreatorAnalyticsReport(
            job_id=job_id,
            creator_username=job.creator_username,
            period_days=job.period_days,
            total_videos=total_videos,
            total_views=creator_total_views,
            average_views_per_video=round(avg_creator_views, 2),
            total_unique_products=len(grouped),
            products=product_performances,
        )
