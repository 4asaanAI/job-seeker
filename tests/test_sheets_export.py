import pytest

from jobpilot.sheets.export import HEADERS, GoogleSheetsExporter


class Request:
    def __init__(self, result, recorder=None):
        self.result = result
        self.recorder = recorder

    def execute(self):
        if self.recorder is not None:
            self.recorder.append(self.result)
        return self.result


class FakeValues:
    def __init__(self, calls):
        self.calls = calls

    def clear(self, **kwargs):
        self.calls.append(("clear", kwargs))
        return Request({})

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return Request({})


class FakeSpreadsheets:
    def __init__(self):
        self.calls = []
        self._values = FakeValues(self.calls)

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return Request({
            "spreadsheetId": "sheet-1",
            "sheets": [{"properties": {"sheetId": 7, "title": "Sheet1", "hidden": False}}],
        })

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return Request({
            "spreadsheetId": kwargs["spreadsheetId"],
            "sheets": [{"properties": {"sheetId": 7, "title": "Tracker", "hidden": False}}],
        })

    def values(self):
        return self._values

    def batchUpdate(self, **kwargs):
        self.calls.append(("batchUpdate", kwargs))
        return Request({})


class FakeSheets:
    def __init__(self):
        self._spreadsheets = FakeSpreadsheets()

    def spreadsheets(self):
        return self._spreadsheets


class FakePermissions:
    def __init__(self, calls):
        self.calls = calls

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return Request({"id": "permission-1"})


class FakeDrive:
    def __init__(self):
        self.calls = []
        self._permissions = FakePermissions(self.calls)

    def permissions(self):
        return self._permissions


def test_export_creates_formats_and_directly_shares_one_way_view():
    sheets = FakeSheets()
    drive = FakeDrive()
    result = GoogleSheetsExporter(sheets, drive).export(
        title="JobPilot - A",
        rows=[{
            "posting_id": 4, "title": "Analyst", "company_name": "Acme",
            "location": "Austin", "url": "javascript:bad", "fit_score": 88,
            "status": "scored", "first_seen_at": "2026-09-19",
            "reasoning": "Good fit", "application_status": None,
        }],
        share_email="a@example.test",
    )

    assert result.spreadsheet_id == "sheet-1"
    assert result.sheet_title == "Sheet1"
    assert result.row_count == 1
    update = next(kwargs for kind, kwargs in sheets._spreadsheets.calls if kind == "update")
    assert update["body"]["values"][0][0].startswith("Summary: ")             # the summary row sits above the header
    assert update["body"]["values"][1] == list(HEADERS)
    assert update["body"]["values"][2][4] == ""
    permission = drive.calls[0]
    assert permission["body"] == {
        "type": "user", "role": "reader", "emailAddress": "a@example.test"
    }
    assert permission["sendNotificationEmail"] is True


def test_existing_multi_tab_sheet_requires_explicit_tab():
    sheets = FakeSheets()
    sheets._spreadsheets.get = lambda **kwargs: Request({
        "sheets": [
            {"properties": {"sheetId": 1, "title": "Jobs", "hidden": False}},
            {"properties": {"sheetId": 2, "title": "Notes", "hidden": False}},
        ]
    })
    with pytest.raises(ValueError, match="sheet_title"):
        GoogleSheetsExporter(sheets, FakeDrive()).export(
            title="JobPilot", rows=[], share_email="a@example.test",
            spreadsheet_id="existing",
        )


def test_invalid_share_email_is_rejected_before_api_calls():
    sheets = FakeSheets()
    with pytest.raises(ValueError, match="share_email"):
        GoogleSheetsExporter(sheets, FakeDrive()).export(
            title="JobPilot", rows=[], share_email="not-an-email",
        )
    assert sheets._spreadsheets.calls == []
