"""
Export Manager (Tahap 7 — PRD Section 2.4 #8 & Section 4.9).

Generates multi-sheet `.xlsx` Excel workbooks with the 4 required PRD worksheets:
1. Creator Summary
2. Product Summary
3. Video Details
4. Extraction Logs

Default filename pattern: `TikTok_Analytics_[username]_[period]_[date].xlsx`
"""

from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple, Union
from xml.sax.saxutils import escape

from shadowkreator.config import DEFAULT_EXPORT_DIR
from shadowkreator.core.analytics import AnalyticsEngine
from shadowkreator.core.database import DatabaseManager


def build_export_filename(
    creator_username: str, period_days: int, export_date: Optional[date] = None
) -> str:
    """Format filename per PRD Section 4.9: `TikTok_Analytics_[username]_[period]_[date].xlsx`."""
    clean_user = creator_username.strip().lstrip("@") or "creator"
    dt_str = (export_date or date.today()).isoformat()
    return f"TikTok_Analytics_{clean_user}_{period_days}d_{dt_str}.xlsx"


class ExportManager:
    """Exports a completed scraping job to a 4-sheet Excel (.xlsx) file."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self.analytics = AnalyticsEngine(db)

    def export_job_to_excel(
        self,
        job_id: int,
        output_path: Optional[Union[str, Path]] = None,
    ) -> Path:
        """
        Build the 4 worksheets specified in PRD Section 4.9 and save to `output_path`.
        """
        job = self.db.get_job(job_id)
        if job is None:
            raise ValueError(f"Scraping job #{job_id} tidak ditemukan.")

        report = self.analytics.build_job_report(job_id)

        if output_path is None:
            DEFAULT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            filename = build_export_filename(job.creator_username, job.period_days)
            target_path = DEFAULT_EXPORT_DIR / filename
        else:
            target_path = Path(output_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)

        # Sheet 1 — Creator Summary (PRD Section 4.9)
        sheet1_rows: List[Sequence[Any]] = [
            [
                "Creator Username",
                "Analysis Period",
                "Total Videos",
                "Total Views",
                "Total Products",
            ],
            [
                report.creator_username,
                f"{report.period_days} Days",
                report.total_videos,
                report.total_views,
                report.total_unique_products,
            ],
            [],
            ["Calculation Note", report.calculation_note],
        ]

        # Sheet 2 — Product Summary (PRD Section 4.9)
        sheet2_rows: List[Sequence[Any]] = [
            [
                "Product Name",
                "Product URL",
                "Total Videos",
                "Total Views",
                "Average Views",
            ]
        ]
        for prod in report.products:
            sheet2_rows.append(
                [
                    prod.product_name,
                    prod.product_url or "Tidak Tersedia",
                    prod.video_count,
                    prod.total_views,
                    prod.average_views,
                ]
            )

        # Sheet 3 — Video Details (PRD Section 4.9)
        sheet3_rows: List[Sequence[Any]] = [
            [
                "Creator Username",
                "Video ID",
                "Video URL",
                "Upload Date",
                "Views",
                "Product Name",
                "Product URL",
            ]
        ]
        with self.db.connection() as conn:
            video_rows = conn.execute(
                """
                SELECT
                    v.creator_username,
                    v.video_id,
                    v.video_url,
                    v.upload_date,
                    v.views,
                    p.product_name,
                    p.product_url
                FROM videos v
                LEFT JOIN video_products vp ON vp.video_id = v.id
                LEFT JOIN products p ON p.id = vp.product_id
                WHERE v.job_id = ?
                ORDER BY v.id ASC, vp.id ASC
                """,
                (job_id,),
            ).fetchall()

        for r in video_rows:
            sheet3_rows.append(
                [
                    r["creator_username"],
                    r["video_id"] or "Tidak Tersedia",
                    r["video_url"],
                    r["upload_date"] or "Tidak Tersedia",
                    r["views"] if r["views"] is not None else "Tidak Tersedia",
                    r["product_name"] or "-",
                    r["product_url"] or ("Tidak Tersedia" if r["product_name"] else "-"),
                ]
            )

        # Sheet 4 — Extraction Logs (PRD Section 4.9)
        sheet4_rows: List[Sequence[Any]] = [
            [
                "Video URL",
                "Extraction Status",
                "Error Message",
                "Scraped At",
            ]
        ]
        videos_by_vid_id = {
            (v.video_id or ""): v.video_url for v in self.db.list_videos(job_id)
        }
        for log in self.db.list_logs(job_id):
            vid_url = videos_by_vid_id.get(log.video_id or "", log.video_id or "-")
            sheet4_rows.append(
                [
                    vid_url,
                    f"{log.log_level} - {log.action}",
                    log.message,
                    log.created_at,
                ]
            )

        sheets: List[Tuple[str, List[Sequence[Any]]]] = [
            ("Creator Summary", sheet1_rows),
            ("Product Summary", sheet2_rows),
            ("Video Details", sheet3_rows),
            ("Extraction Logs", sheet4_rows),
        ]

        try:
            import openpyxl
            from openpyxl.styles import Font

            wb = openpyxl.Workbook()
            # Remove default sheet
            wb.remove(wb.active)

            for title, rows in sheets:
                ws = wb.create_sheet(title=title)
                for row_idx, row_values in enumerate(rows, start=1):
                    ws.append(list(row_values))
                    if row_idx == 1:
                        for cell in ws[1]:
                            cell.font = Font(bold=True)

            wb.save(str(target_path))
        except ImportError:
            _write_minimal_xlsx(target_path, sheets)

        return target_path


def _col_letter(col_idx: int) -> str:
    """Convert 1-based column index to Excel column letters (A, B, ..., Z, AA...)."""
    result = ""
    while col_idx > 0:
        col_idx, rem = divmod(col_idx - 1, 26)
        result = chr(65 + rem) + result
    return result


def _write_minimal_xlsx(
    target_path: Path, sheets: Sequence[Tuple[str, Sequence[Sequence[Any]]]]
) -> None:
    """Write a standards-compliant OpenXML `.xlsx` file using Python's built-in zipfile."""
    with zipfile.ZipFile(target_path, "w", zipfile.ZIP_DEFLATED) as zf:
        content_types = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
            '  <Default Extension="xml" ContentType="application/xml"/>',
            '  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        ]
        for idx in range(1, len(sheets) + 1):
            content_types.append(
                f'  <Override PartName="/xl/worksheets/sheet{idx}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
        content_types.append("</Types>")
        zf.writestr("[Content_Types].xml", "\n".join(content_types))

        root_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>\n'
            "</Relationships>"
        )
        zf.writestr("_rels/.rels", root_rels)

        wb_sheets = []
        wb_rels = []
        for idx, (title, _) in enumerate(sheets, start=1):
            safe_title = escape(title)
            wb_sheets.append(
                f'    <sheet name="{safe_title}" sheetId="{idx}" r:id="rId{idx}"/>'
            )
            wb_rels.append(
                f'  <Relationship Id="rId{idx}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{idx}.xml"/>'
            )

        workbook_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
            "  <sheets>\n"
            + "\n".join(wb_sheets)
            + "\n  </sheets>\n</workbook>"
        )
        zf.writestr("xl/workbook.xml", workbook_xml)

        workbook_rels_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            + "\n".join(wb_rels)
            + "\n</Relationships>"
        )
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)

        for idx, (_, rows) in enumerate(sheets, start=1):
            sheet_lines = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
                "  <sheetData>",
            ]
            for r_idx, row in enumerate(rows, start=1):
                sheet_lines.append(f'    <row r="{r_idx}">')
                for c_idx, val in enumerate(row, start=1):
                    cell_ref = f"{_col_letter(c_idx)}{r_idx}"
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        sheet_lines.append(
                            f'      <c r="{cell_ref}"><v>{val}</v></c>'
                        )
                    else:
                        text_val = escape(str(val if val is not None else ""))
                        sheet_lines.append(
                            f'      <c r="{cell_ref}" t="inlineStr"><is><t>{text_val}</t></is></c>'
                        )
                sheet_lines.append("    </row>")
            sheet_lines.append("  </sheetData>")
            sheet_lines.append("</worksheet>")
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", "\n".join(sheet_lines))
