"""One-way JobPilot-to-Google-Sheets export.

The SQLite store remains authoritative. This module only writes a rendered
view and shares it directly with the supplied seeker's email address; it never
creates an anyone-with-the-link permission.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..sources.base import safe_url

HEADERS = (
    "Posting ID", "Title", "Company", "Location", "URL", "Score",
    "Job Status", "First Seen", "Reasoning", "Application Status",
)


@dataclass(frozen=True)
class ExportResult:
    spreadsheet_id: str
    spreadsheet_url: str
    sheet_title: str
    row_count: int
    shared_with: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "spreadsheet_id": self.spreadsheet_id,
            "spreadsheet_url": self.spreadsheet_url,
            "sheet_title": self.sheet_title,
            "row_count": self.row_count,
            "shared_with": self.shared_with,
        }


def load_google_services(service_account_path: Path):
    """Build Sheets and Drive clients from an operator-owned service account."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - packaging/environment path
        raise RuntimeError(
            "Google export requires the 'sheets' optional dependencies"
        ) from exc
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = service_account.Credentials.from_service_account_file(
        str(service_account_path), scopes=scopes
    )
    sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    return sheets, drive


def _email(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
        raise ValueError("share_email must be a valid direct email address")
    return text


def _title(value: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError("sheet title must not be empty")
    return text[:100]


def _a1(sheet_title: str, cell_range: str) -> str:
    return "'" + sheet_title.replace("'", "''") + "'!" + cell_range


# Status colours for the Job Status and Application Status columns (light fills, readable text).
STATUS_COLOURS = {
    "applied": (0.85, 0.94, 0.85), "interview": (0.72, 0.89, 0.72), "offer": (0.58, 0.83, 0.58),
    "started": (1.0, 0.93, 0.8), "rejected": (0.96, 0.8, 0.8), "withdrawn": (0.9, 0.9, 0.9),
    "expired": (0.9, 0.9, 0.9), "skipped": (0.9, 0.9, 0.9), "presented": (0.82, 0.89, 0.98),
    "scored": (0.82, 0.89, 0.98),
}


def summary_row(rows: list[dict[str, Any]]) -> list[str]:
    """Row 1: totals per application status, so the sheet answers "how am I doing" at a glance."""
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("application_status") or "")
        if status:
            counts[status] = counts.get(status, 0) + 1
    parts = [f"{len(rows)} jobs"] + [f"{n} {k}" for k, n in sorted(counts.items())]
    return ["Summary: " + ", ".join(parts)] + [""] * (len(HEADERS) - 1)


def _style_requests(sheet_id: int, rows: int) -> list[dict]:
    """Conditional fills by status on the two status columns, and auto-sized columns."""
    requests = []
    for column in (HEADERS.index("Job Status"), HEADERS.index("Application Status")):
        for status, (r, g, b) in STATUS_COLOURS.items():
            requests.append({"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [{"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": rows + 2,
                            "startColumnIndex": column, "endColumnIndex": column + 1}],
                "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": status}]},
                                "format": {"backgroundColor": {"red": r, "green": g, "blue": b}}}}}})
    requests.append({"autoResizeDimensions": {"dimensions": {
        "sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": len(HEADERS)}}})
    return requests


def _row_values(row: dict[str, Any]) -> list[str]:
    score = row.get("fit_score")
    return [
        str(row.get("posting_id") or ""),
        str(row.get("title") or ""),
        str(row.get("company_name") or ""),
        str(row.get("location") or ""),
        safe_url(str(row.get("url") or "")) or "",
        "" if score is None else str(score),
        str(row.get("status") or ""),
        str(row.get("first_seen_at") or ""),
        str(row.get("reasoning") or ""),
        str(row.get("application_status") or ""),
    ]


class GoogleSheetsExporter:
    """Export rows using injected Google API-shaped clients for easy testing."""

    def __init__(self, sheets_service: Any, drive_service: Any):
        self.sheets = sheets_service
        self.drive = drive_service

    def _metadata(self, spreadsheet_id: str) -> tuple[str, int]:
        response = self.sheets.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="spreadsheetId,sheets(properties(sheetId,title,hidden))",
        ).execute()
        sheets = [
            sheet["properties"] for sheet in response.get("sheets", [])
            if not sheet.get("properties", {}).get("hidden", False)
        ]
        if len(sheets) != 1:
            raise ValueError("sheet_title is required when the spreadsheet has multiple visible tabs")
        return str(sheets[0]["title"]), int(sheets[0]["sheetId"])

    def export(
        self,
        *,
        title: str,
        rows: list[dict[str, Any]],
        share_email: str,
        spreadsheet_id: str | None = None,
        sheet_title: str | None = None,
    ) -> ExportResult:
        """Create/update a sheet and grant only a direct reader permission."""
        email = _email(share_email)
        title = _title(title)
        if spreadsheet_id:
            spreadsheet_id = str(spreadsheet_id).strip()
            if not spreadsheet_id:
                raise ValueError("spreadsheet_id must not be empty")
            created_here = False
        else:
            created_here = True
            created = self.sheets.spreadsheets().create(
                body={"properties": {"title": title}},
                fields="spreadsheetId,sheets(properties(sheetId,title,hidden))",
            ).execute()
            spreadsheet_id = str(created["spreadsheetId"])

        if sheet_title is None:
            if spreadsheet_id and "created" in locals():
                visible = [
                    item["properties"] for item in created.get("sheets", [])
                    if not item.get("properties", {}).get("hidden", False)
                ]
                if len(visible) != 1:
                    raise ValueError("new spreadsheet did not return exactly one visible tab")
                sheet_title = str(visible[0]["title"])
                sheet_id = int(visible[0]["sheetId"])
            else:
                sheet_title, sheet_id = self._metadata(spreadsheet_id)
        else:
            metadata = self.sheets.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="spreadsheetId,sheets(properties(sheetId,title,hidden))",
            ).execute()
            matches = [
                item["properties"] for item in metadata.get("sheets", [])
                if item.get("properties", {}).get("title") == sheet_title
                and not item.get("properties", {}).get("hidden", False)
            ]
            if len(matches) != 1:
                raise ValueError("sheet_title does not identify exactly one visible tab")
            sheet_id = int(matches[0]["sheetId"])

        values = [summary_row(rows), list(HEADERS), *[_row_values(row) for row in rows]]
        range_name = _a1(str(sheet_title), "A:J")
        self.sheets.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=range_name, body={}
        ).execute()
        self.sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=_a1(str(sheet_title), "A1"),
            valueInputOption="RAW",
            body={"majorDimension": "ROWS", "values": values},
        ).execute()
        self.sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [
                {"repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                              "startColumnIndex": 0, "endColumnIndex": len(HEADERS)},
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "italic": True}}},
                    "fields": "userEnteredFormat(textFormat)",
                }},
                {"repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 1,
                              "endRowIndex": 2, "startColumnIndex": 0,
                              "endColumnIndex": len(HEADERS)},
                    "cell": {"userEnteredFormat": {
                        "backgroundColor": {"red": 0.12, "green": 0.47, "blue": 0.71},
                        "textFormat": {"bold": True, "foregroundColor": {
                            "red": 1, "green": 1, "blue": 1}},
                    }},
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }},
                {"updateSheetProperties": {
                    "properties": {"sheetId": sheet_id,
                                   "gridProperties": {"frozenRowCount": 2}},
                    "fields": "gridProperties.frozenRowCount",
                }},
                # colour rules are added once, when the sheet is created: repeating them on every export piles them up
                *(_style_requests(sheet_id, len(rows)) if created_here else _style_requests(sheet_id, len(rows))[-1:]),
            ]},
        ).execute()
        self.drive.permissions().create(
            fileId=spreadsheet_id,
            body={"type": "user", "role": "reader", "emailAddress": email},
            fields="id",
            sendNotificationEmail=True,
        ).execute()
        return ExportResult(
            spreadsheet_id=spreadsheet_id,
            spreadsheet_url=f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
            sheet_title=str(sheet_title), row_count=len(rows), shared_with=email,
        )
