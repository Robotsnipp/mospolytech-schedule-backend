"""Regressions for fresh example data and a solver-independent saved-result audit."""

import json
from datetime import date, time
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management.base import CommandError
from django.test import TestCase

from schedule_generator.enums import LessonType
from schedule_generator.management.commands.seed_generation_example import seed_example
from schedule_generator.models import (
    GenerationRun,
    Group,
    LessonEvent,
    Room,
    ScheduledClass,
    Semester,
    Student,
    Subject,
    Teacher,
    TimeSlot,
    WeekPattern,
)
from schedule_generator.services.generation import run_generation
from schedule_generator.services.generation_audit import audit_run


def write_source(directory):
    digest = "ab" * 32
    document = {
        "schema_version": 1, "source": {"sha256": digest, "filename": "synthetic-test.json"},
        "metadata": {"program_code": "09.03.01", "profile_name": "Тест", "admission_year": 2025,
                     "study_form_raw": "Очная"},
        "entries": [{
            "source_row": 1, "discipline_code": "Б1.1", "subject": "Тестовая дисциплина",
            "semester_loads": [{"semester_number": 3, "lecture_hours": 0,
                                "practice_hours": 36, "lab_hours": 0, "contact_hours": 36}],
            "totals": {}, "controls": {},
        }],
    }
    review = {"reviewed": True, "source_sha256": digest, "duration_months": 48,
              "entries": [{"source_row": 1, "semester_number": 3, "activity_kind": "REGULAR"}]}
    projects = [
        {"id": i + 1, "title": f"Тестовый проект {i + 1}", "day": i, "start": "10:45",
         "end": "12:15", "teacher_id": i + 2, "student_ids": list(range(i * 5 + 1, i * 5 + 6)),
         "buffer": 30}
        for i in range(6)
    ]
    data = {
        "review": review, "admission_year": 2025, "semester_number": 3,
        "students": [{"id": i, "name": f"Вымышленный студент {i}"} for i in range(1, 31)],
        "teachers": [{"id": i, "full_name": f"Тестовый преподаватель {i}"} for i in range(1, 8)],
        "projects": projects, "fullness": [{"entry_id": 77, "source_row": 1}],
        "modules": [
            {"number": 1, "start_date": "2026-09-01", "end_date": "2026-10-31", "teaching_weeks": 9},
            {"number": 2, "start_date": "2026-11-01", "end_date": "2026-12-31", "teaching_weeks": 9},
        ],
    }
    files = {"01-import-request.json": {"document": document}, "data.json": data}
    for number in (1, 2):
        files[f"02-prepare-module-{number}-request.json"] = {
            "academic_hours_per_pair": 2,
            "entries": [{"curriculum_entry_id": 77, "teacher_id": 1,
                         "lesson_type": "PRACTICE", "academic_hours": 18}],
        }
    for name, value in files.items():
        (directory / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class GenerationExampleTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        write_source(self.directory)

    def test_fresh_seed_uses_seven_official_pairs_and_real_solver(self):
        result = seed_example(source_dir=self.directory, semester_name="Тест звонков", include_synthetic_pd=True)
        pattern = WeekPattern.objects.get(pk=result["week_pattern_id"])
        self.assertEqual(pattern.slots.count(), 42)
        self.assertEqual(pattern.slots.get(day_of_week=0, lesson_number=2).start_time, time(10, 40))
        self.assertEqual(pattern.slots.get(day_of_week=0, lesson_number=4).start_time, time(14, 30))
        self.assertEqual(pattern.slots.get(day_of_week=0, lesson_number=7).end_time, time(21))
        self.assertEqual(Student.objects.count(), 30)
        self.assertIn("example_notice", Group.objects.get(pk=result["group_id"]).curriculum.source)
        for module in result["modules"]:
            outcome = run_generation(
                semester=pattern.semester, module=pattern.semester.modules.get(pk=module["module_id"]),
                week_pattern=pattern, seed=17, time_limit_seconds=5, clear_previous=False,
            )
            audit = audit_run(GenerationRun.objects.get(pk=outcome.run_id))
            self.assertTrue(audit["passed"], audit)
            self.assertEqual(audit["required_hours"], 18)
            self.assertEqual(audit["placed_hours"], 18)
            self.assertEqual(audit["fixed_pd_count"], 6)
        before = list(TimeSlot.objects.values())
        with self.assertRaises(CommandError):
            seed_example(source_dir=self.directory, semester_name="Тест звонков")
        self.assertEqual(list(TimeSlot.objects.values()), before)

    def test_invalid_late_load_rolls_back_seed(self):
        path = self.directory / "02-prepare-module-2-request.json"
        payload = json.loads(path.read_text())
        payload["entries"][0]["academic_hours"] = 17
        path.write_text(json.dumps(payload))
        with self.assertRaises(ValueError):
            seed_example(source_dir=self.directory, semester_name="Откат")
        self.assertFalse(Semester.objects.exists())
        self.assertFalse(Student.objects.exists())

    def test_seed_keeps_historical_slots_events_and_saved_result_unchanged(self):
        semester = Semester.objects.create(name="Исторический пример", start_date=date(2026, 9, 1))
        pattern = WeekPattern.objects.create(semester=semester)
        old_slot = TimeSlot.objects.create(
            week_pattern=pattern, day_of_week=1, lesson_number=2,
            start_time=time(10, 45), end_time=time(12, 15),
        )
        old_event = LessonEvent.objects.create(
            semester=semester, group=Group.objects.create(name="251-998", semester=semester),
            teacher=Teacher.objects.create(full_name="Старый преподаватель"),
            subject=Subject.objects.create(name="Старый предмет"),
            lesson_type=LessonType.PRACTICE, pairs_per_week=1,
        )
        run = GenerationRun.objects.create(semester=semester, status="SOLVED")
        saved = ScheduledClass.objects.create(event=old_event, slot=old_slot, run=run)
        result = seed_example(source_dir=self.directory, semester_name="Новый пример")
        old_slot.refresh_from_db()
        saved.refresh_from_db()
        self.assertEqual((old_slot.start_time, old_slot.end_time), (time(10, 45), time(12, 15)))
        self.assertEqual(saved.slot_id, old_slot.pk)
        self.assertEqual(saved.event_id, old_event.pk)
        self.assertNotEqual(result["semester_id"], semester.pk)
        self.assertFalse(LessonEvent.objects.filter(
            semester_id=result["semester_id"], pk=old_event.pk,
        ).exists())


class GenerationAuditTests(TestCase):
    def setUp(self):
        self.semester = Semester.objects.create(name="Аудит", start_date=date(2026, 9, 1), weeks_count=9)
        self.pattern = WeekPattern.objects.create(semester=self.semester)
        self.group = Group.objects.create(name="251-999", semester=self.semester)
        self.student = Student.objects.create(group=self.group, full_name="Тест")
        self.teacher = Teacher.objects.create(full_name="Тестовый преподаватель")
        self.room = Room.objects.create(name="Тестовая аудитория")
        self.subject = Subject.objects.create(name="Тест")
        self.run = GenerationRun.objects.create(semester=self.semester, status="SOLVED")

    def pair(self, number, start, end):
        event = LessonEvent.objects.create(
            semester=self.semester, group=self.group, teacher=self.teacher, subject=self.subject,
            lesson_type=LessonType.PRACTICE, pairs_per_week=1, academic_hours=18,
            preparation_source={"teaching_weeks": 9, "academic_hours_per_pair": 2},
        )
        slot = TimeSlot.objects.create(
            week_pattern=self.pattern, day_of_week=1, lesson_number=number,
            start_time=start, end_time=end,
        )
        return ScheduledClass.objects.create(event=event, slot=slot, room=self.room, run=self.run)

    def test_audit_detects_actual_minute_overlap_even_with_distinct_slot_numbers(self):
        self.pair(1, time(9), time(10, 30))
        self.pair(2, time(10), time(11, 30))
        audit = audit_run(self.run)
        self.assertFalse(audit["passed"])
        for key in ("students", "groups", "teachers", "rooms"):
            self.assertEqual(audit["conflicts"][key], 1, key)
        self.assertEqual(audit["hour_mismatches"], [])

    def test_audit_checks_fixed_pd_buffer_without_claiming_online_travel(self):
        pair = self.pair(3, time(12, 20), time(13, 50))
        self.run.fixed_pd_snapshot = [{
            "session_id": 1, "day_of_week": 1, "start": "14:00", "end": "15:30",
            "student_ids": [self.student.pk], "teacher_id": None, "room_id": None,
            "travel_buffer_minutes": 30,
        }]
        audit = audit_run(self.run)
        self.assertEqual(audit["conflicts"]["students"], 0)
        self.assertEqual(audit["conflicts"]["pd_buffer"], 1)
        pair.event.lesson_type = LessonType.LECTURE
        pair.event.format = "ONLINE"
        pair.event.save()
        self.assertEqual(audit_run(self.run)["conflicts"]["pd_buffer"], 0)
