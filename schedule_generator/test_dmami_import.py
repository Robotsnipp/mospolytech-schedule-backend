"""Database import preserves source data and does not modify solver schedules."""

import copy
import json
from datetime import date

from django.test import TestCase

from schedule_generator.models import (
    GenerationRun,
    ImportedGroupSchedule,
    ImportedLesson,
    ScheduleImport,
)
from schedule_generator.services.dmami_import import import_dmami_schedule


def document():
    return {"kind": "dmami_schedule", "schema_version": 1, "complete": True,
            "exported_count": 1, "errors": [],
            "source_metadata": {"date": "2025-04-30"},
            "source": {"url": "https://rasp.dmami.ru/semester.json"},
            "groups": [{"name": "241-3210", "group": {"title": "241-3210"},
                        "lessons": [{"group": "241-3210", "source_path": "grid.5.3[0]",
                                     "day_of_week": 5, "lesson_number": 3, "subject": "Test",
                                     "date_from": "2025-02-03", "date_to": "2025-06-08",
                                     "start": "12:20", "end": "13:50", "week_raw": "odd",
                                     "teachers": [{"id": 123, "name": "Teacher"}],
                                     "rooms": ["A", "B"], "raw": {"original": True},
                                     "warnings": ["uninterpreted_week_pattern"]}]}]}


def encode(value):
    return json.dumps(value).encode()


class DMAMIImportTests(TestCase):
    def test_import_and_repeat_keep_dates_provenance_and_multiple_rooms(self):
        data = document()
        first = import_dmami_schedule(encode(data))
        again = import_dmami_schedule(encode(data))
        self.assertEqual(first["import_id"], again["import_id"])
        self.assertFalse(again["created"])
        self.assertEqual(ScheduleImport.objects.count(), 1)
        self.assertEqual(ImportedGroupSchedule.objects.count(), 1)
        lesson = ImportedLesson.objects.get()
        self.assertEqual(lesson.date_from, date(2025, 2, 3))
        self.assertEqual(lesson.start.hour, 12)
        self.assertEqual(lesson.data, data["groups"][0]["lessons"][0])
        self.assertEqual(ScheduleImport.objects.get().source_metadata["date"], "2025-04-30")
        self.assertEqual(GenerationRun.objects.count(), 0)

    def test_invalid_later_group_rolls_back_entire_import(self):
        data = document()
        data["groups"].append(copy.deepcopy(data["groups"][0]))
        data["exported_count"] = 2
        with self.assertRaises(ValueError):
            import_dmami_schedule(encode(data))
        self.assertEqual(ScheduleImport.objects.count(), 0)
        self.assertEqual(ImportedLesson.objects.count(), 0)

    def test_dry_run_writes_nothing(self):
        result = import_dmami_schedule(encode(document()), dry_run=True)
        self.assertEqual(result["lessons"], 1)
        self.assertIsNone(result["import_id"])
        self.assertEqual(ScheduleImport.objects.count(), 0)
        self.assertEqual(ImportedGroupSchedule.objects.count(), 0)

    def test_incomplete_export_and_duplicate_source_paths_are_rejected(self):
        for mode in ("incomplete", "duplicate"):
            data = document()
            if mode == "incomplete":
                data["complete"] = False
            else:
                data["groups"][0]["lessons"] *= 2
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                import_dmami_schedule(encode(data))
        self.assertEqual(ScheduleImport.objects.count(), 0)

    def test_unknown_dates_remain_in_original_data(self):
        data = document()
        data["groups"][0]["lessons"][0]["date_from"] = "unreadable"
        import_dmami_schedule(encode(data))
        lesson = ImportedLesson.objects.get()
        self.assertIsNone(lesson.date_from)
        self.assertEqual(lesson.data["date_from"], "unreadable")
