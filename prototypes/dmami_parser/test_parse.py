"""Regression tests of catalog extraction, source fidelity and incomplete exports."""

import base64
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from prototypes.dmami_parser.parse import (
    HAR,
    Network,
    Semester,
    digest,
    export,
    parse_bells,
    parse_catalog,
    parse_group,
)


def javascript():
    blocks = []
    for mode in range(3):
        slots = ",".join(f"{slot}: '9:00-10:30'" for slot in range(1, 8))
        blocks.append(f"'{mode}': {{{slots}}}")
    return "var times = {" + ",".join(blocks) + "\n};"


def homepage():
    return ('<script>var globalListGroups = '
            '{"date":"2026-10-20","groups":{"A":false,"B":true}}.groups;</script>')


def payload(name="A"):
    lesson = {"sbj": "Subject, subgroup 1", "type": "Practice",
              "teachers": [{"id": 7, "name": "Teacher"}], "teacher": "Teacher",
              "df": "2026-09-01", "dt": "2027-01-03", "week": "",
              "auditories": [{"title": '<a href="https://example.org/?a=1&amp;b=2">Online</a>',
                              "color": "#00f"}], "e_link": None,
              "shortRooms": [], "location": "Webinar"}
    return {"status": "ok", "group": {"title": name, "evening": 0},
            "isSession": False, "grid": {"1": {"1": [lesson, {**lesson, "df": "2026-11-01"}]}}}


class FakeSource:
    def get(self, path):
        if path == "/":
            return homepage().encode(), "catalog-time"
        if path == "/js/main.js":
            return javascript().encode(), "javascript-time"
        if "group=A&" in path:
            return json.dumps(payload()).encode(), "group-time"
        return b'{"status":"error","message":"Unavailable"}', "error-time"


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.bells = parse_bells(javascript())

    def test_catalog_json_assignment_and_boolean_flags(self):
        self.assertEqual(parse_catalog(homepage())["groups"], {"A": False, "B": True})
        with self.assertRaises(ValueError):
            parse_catalog('<script>var globalListGroups = {};</script>')

    def test_bell_profile_is_read_and_incomplete_source_rejected(self):
        self.assertEqual(self.bells["2"]["7"], {"start": "09:00", "end": "10:30"})
        with self.assertRaises(ValueError):
            parse_bells(javascript().replace("7:", "8:"))

    def test_preserves_all_rules_teachers_and_embedded_links(self):
        source = payload()
        result = parse_group(source, "A", self.bells)
        self.assertEqual(len(result["lessons"]), 2)
        lesson = result["lessons"][0]
        self.assertEqual(lesson["links"], ["https://example.org/?a=1&b=2"])
        self.assertEqual(lesson["raw"], source["grid"]["1"]["1"][0])
        self.assertEqual(lesson["source_path"], "grid.1.1[0]")
        self.assertEqual(lesson["teachers"][0]["id"], 7)
        self.assertTrue(lesson["review_required"])

    def test_unknown_week_and_bell_mode_are_not_guessed(self):
        source = payload()
        source["group"]["evening"] = 99
        source["grid"]["1"]["1"][0]["week"] = "odd"
        result = parse_group(source, "A", self.bells)
        self.assertIn("unknown_bell_mode", result["warnings"])
        self.assertIsNone(result["lessons"][0]["start"])
        self.assertIn("uninterpreted_week_pattern", result["lessons"][0]["warnings"])

    def test_dates_warn_without_changing_source(self):
        source = payload()
        source["grid"]["1"]["1"][0]["df"] = "unreadable"
        lesson = parse_group(source, "A", self.bells)["lessons"][0]
        self.assertEqual(lesson["date_from"], "unreadable")
        self.assertIn("invalid_df", lesson["warnings"])

    def test_api_error_mismatch_and_session_are_rejected(self):
        for source in ({"status": "error", "message": "No group"},
                       payload("different"), {**payload(), "isSession": True}):
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse_group(source, "A", self.bells)

    def test_empty_schedule_is_valid_not_a_download_failure(self):
        self.assertEqual(parse_group({**payload(), "grid": []}, "A", self.bells)["lessons"], [])

    def test_partial_export_keeps_sources_and_reports_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = export(FakeSource(), root)
            self.assertFalse(result["complete"])
            self.assertFalse(result["covers_entire_catalog"])
            self.assertEqual(result["exported_count"], 1)
            self.assertEqual(result["errors"][0]["group"], "B")
            self.assertEqual(len(list((root / "source/groups").glob("*.json"))), 2)
            saved = json.loads((root / "schedule.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["groups"][0]["source"]["captured_at"], "group-time")
            self.assertEqual(saved["groups"][0]["source"]["sha256"],
                             digest(json.dumps(payload()).encode()))
            with (root / "schedule.csv").open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 2)

    def test_selected_export_does_not_claim_entire_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = export(FakeSource(), root, ["A", "A"])
            self.assertTrue(result["complete"])
            self.assertFalse(result["covers_entire_catalog"])
            self.assertEqual(result["requested_count"], 1)

    def test_csv_formula_and_group_path_are_safe(self):
        class UntrustedSource(FakeSource):
            def get(self, path):
                if path == "/":
                    return homepage().replace('"A":false', '"../A":false').encode(), "time"
                if path.startswith("/site/group"):
                    data = payload("../A")
                    data["grid"]["1"]["1"][0]["sbj"] = "=1+1"
                    return json.dumps(data).encode(), "time"
                return super().get(path)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = export(UntrustedSource(), root, ["../A"])
            self.assertTrue(result["complete"])
            file = result["groups"][0]["file"]
            self.assertEqual(file, f"groups/{digest(b'../A')}.json")
            with (root / "schedule.csv").open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(next(csv.DictReader(stream))["subject"], "'=1+1")
            self.assertEqual(result["groups"][0]["lessons"][0]["subject"], "=1+1")

    def test_har_replay_handles_base64_and_ignores_secrets(self):
        entries = []
        for path, body in [("/", homepage()), ("/js/main.js", javascript()),
                           ("/site/group?session=0&group=A", json.dumps(payload()))]:
            entries.append({"request": {"method": "GET", "url": "https://rasp.dmami.ru" + path,
                                         "headers": [{"name": "Cookie", "value": "SECRET"}]},
                            "response": {"status": 200, "content": {
                                "encoding": "base64", "text": base64.b64encode(body.encode()).decode()}},
                            "startedDateTime": "2026-10-20T12:00:00Z"})
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            har = root / "input.har"
            har.write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
            result = export(HAR(har), root / "output")
            self.assertEqual(result["mode"], "har")
            self.assertTrue(result["complete"])
            self.assertEqual(result["requested_groups"], ["A"])
            for path in (root / "output").rglob("*"):
                if path.is_file():
                    self.assertNotIn("SECRET", path.read_text(encoding="utf-8-sig"))

    def test_bulk_downloads_once_and_exports_every_group(self):
        class BulkNetwork:
            def __init__(self):
                self.paths = []

            def get(self, path):
                self.paths.append(path)
                if path == "/semester.json":
                    return json.dumps({"date": "2026-10-20", "groups": {
                        "A": payload("A"), "B": payload("B")}}).encode(), "bulk-time"
                if path == "/js/main.js":
                    return javascript().encode(), "js-time"
                raise AssertionError(f"Unexpected per-group request: {path}")

        network = BulkNetwork()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = export(Semester(network), root)
            self.assertTrue(result["covers_entire_catalog"])
            self.assertEqual(result["exported_count"], 2)
            self.assertEqual(network.paths, ["/semester.json", "/js/main.js"])
            source = result["groups"][0]["source"]
            self.assertEqual(source["json_pointer"], "/groups/A")
            self.assertEqual(source["url"], "https://rasp.dmami.ru/semester.json")
            self.assertEqual(source["document"]["sha256"],
                             digest((root / "source/semester.json").read_bytes()))

    def test_local_bulk_formats_and_rejects_silent_group_loss(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "semester.json"
            for document in ({"A": payload()}, {"schedules": {"A": payload()}},
                             {"groups": {"A": {"title": "A", "evening": 0, "grid": {}}}}):
                path.write_text(json.dumps(document), encoding="utf-8")
                source = Semester(None, path)
                body, _ = source.get("/site/group?group=A&session=0")
                self.assertEqual(json.loads(body)["group"]["title"], "A")
            path.write_text('{"groups":{"A":{},"B":{"grid":{}}}}', encoding="utf-8")
            snapshot = root / "source/semester.json"
            with self.assertRaises(ValueError):
                Semester(None, path, snapshot=snapshot)
            self.assertEqual(snapshot.read_bytes(), path.read_bytes())

    def test_real_contents_wrapper_without_status_or_teacher_ids(self):
        entry = payload()
        del entry["status"]
        del entry["grid"]["1"]["1"][0]["teachers"]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "semester.json"
            js = root / "main.js"
            js.write_text(javascript(), encoding="utf-8")
            path.write_text(json.dumps({"date": "2025-04-30", "time": "21:59:01",
                                       "v": "0.1", "contents": {"A": entry}}), encoding="utf-8")
            result = export(Semester(None, path, js), root / "output")
            self.assertTrue(result["covers_entire_catalog"])
            self.assertEqual(result["source_metadata"]["date"], "2025-04-30")
            self.assertEqual(result["groups"][0]["source"]["json_pointer"], "/contents/A")
            lesson = result["groups"][0]["lessons"][0]
            self.assertEqual(lesson["teachers"], [])
            self.assertEqual(lesson["teacher_raw"], "Teacher")

    @patch("prototypes.dmami_parser.parse.time.sleep")
    @patch("prototypes.dmami_parser.parse.urlopen",
           side_effect=HTTPError("https://rasp.dmami.ru/site/group", 500,
                                 "use /semester.json or /session-file.json", {}, None))
    def test_disabled_legacy_api_does_not_retry_every_error(self, request, sleep):
        with self.assertRaisesRegex(ValueError, "Group API disabled"):
            Network(timeout=1, retries=2, delay=0.5).get("/site/group?group=A&session=0")
        self.assertEqual(request.call_count, 1)

    @patch("prototypes.dmami_parser.parse.time.sleep")
    @patch("prototypes.dmami_parser.parse.urlopen", side_effect=URLError("offline"))
    def test_network_retries_are_bounded(self, request, sleep):
        with self.assertRaises(URLError):
            Network(timeout=1, retries=2, delay=0.5).get("/")
        self.assertEqual(request.call_count, 3)


if __name__ == "__main__":
    unittest.main()
