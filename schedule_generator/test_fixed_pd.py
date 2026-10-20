from copy import deepcopy
from datetime import date, time
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from schedule_generator.api.serializers import GenerationRunSerializer
from schedule_generator.enums import LessonType
from schedule_generator.models import (
    FixedProjectSession,
    GenerationRun,
    Group,
    LectureStream,
    LessonEvent,
    Module,
    Project,
    ProjectEnrollment,
    Room,
    ScheduledClass,
    Semester,
    Student,
    Subject,
    Teacher,
    TimeSlot,
    WeekPattern,
)
from schedule_generator.services.generation import GenerationError, run_generation
from schedule_generator.services.pd_import import PDImportError, import_fixed_pd


class FixedPDTests(TestCase):
    def setUp(self):
        # Проверяем интервалы и конфликты независимо от общего резервирования
        # пн/ср под ПД: оно иначе маскирует ошибку блокировок обычных пар.
        # Сам CP-SAT и остальные ограничения работают без подмены.
        self.day_rules = patch("schedule_generator.services.event_specs.allowed_days_for",
                               return_value=frozenset(range(6)))
        self.day_rules.start()
        self.addCleanup(self.day_rules.stop)
        self.semester = Semester.objects.create(name="2026 autumn", start_date=date(2026, 9, 1))
        self.module = Module.objects.create(semester=self.semester, number=1,
            start_date=date(2026, 9, 1), end_date=date(2026, 10, 31), teaching_weeks=9)
        self.pattern = WeekPattern.objects.create(semester=self.semester)
        for number, start, end in ((1, (12, 20), (13, 50)), (2, (14, 30), (16, 0)),
                                    (3, (16, 10), (17, 40)), (4, (17, 50), (19, 20))):
            TimeSlot.objects.create(week_pattern=self.pattern, day_of_week=0,
                                   lesson_number=number, start_time=time(*start), end_time=time(*end))
        self.group = Group.objects.create(name="G1")
        self.other_group = Group.objects.create(name="G2")
        self.student = Student.objects.create(group=self.group, full_name="Participant")
        self.teacher = Teacher.objects.create(full_name="PD teacher")
        self.other_teacher = Teacher.objects.create(full_name="Subject teacher")
        self.project = Project.objects.create(semester=self.semester, title="Project", supervisor=self.teacher)
        ProjectEnrollment.objects.create(project=self.project, student=self.student)
        self.subject = Subject.objects.create(name="Subject")
        self.room = Room.objects.create(name="Room 1", capacity=40)
        self.other_room = Room.objects.create(name="Room 2", capacity=40)
        self.place = {"kind": "additional_address", "raw": "НАМИ, ул. Автомоторная, 2, Б204",
                      "venue": {"kind": "additional"}, "room_candidates": [],
                      "resource_policy": {"reserve_room": False, "allow_new_assignments": False}}

    def fixed(self, **overrides):
        values = dict(project=self.project, day_of_week=0, start_time=time(13), end_time=time(16),
                      travel_buffer_minutes=0, place_details=deepcopy(self.place), source_key="manual:test")
        values.update(overrides)
        return FixedProjectSession.objects.create(**values)

    def lesson(self, **overrides):
        values = dict(semester=self.semester, subject=self.subject, group=self.group,
                      teacher=self.other_teacher, lesson_type=LessonType.PRACTICE)
        values.update(overrides)
        return LessonEvent.objects.create(**values)

    def generate(self, **overrides):
        return run_generation(semester=self.semester, module=self.module, time_limit_seconds=2, **overrides)

    def only_slots(self, *numbers):
        self.pattern.slots.exclude(lesson_number__in=numbers).delete()

    def document(self):
        return {"kind": "pd", "schema_version": 1, "parser_revision": 4, "academic_year": "2026/2027",
                "source": {"sha256": "a" * 64, "url": "https://example.org/source"},
                "projects": [{"source_number": 6, "teacher_raw": "PD teacher", "schedules": [
                    {"source_cell": {"a1": "G11"}, "notes": [], "changes": [], "meetings": [
                        {"day_of_week": 0, "start": "13:00", "end": "16:00", "recurrence": "weekly_candidate",
                         "date": None, "place_details": deepcopy(self.place), "teacher_override_raw": []}]}]}]}

    def mapping(self):
        return {"reviewed": True, "source_sha256": "a" * 64,
                "projects": [{"source_number": 6, "project_id": self.project.pk, "teacher_id": self.teacher.pk}]}

    def import_document(self, document=None, mapping=None):
        return import_fixed_pd(document=document or self.document(), mapping=mapping or self.mapping(),
                               semester=self.semester, module=self.module, travel_buffer_minutes=20)

    def test_fixed_pd_blocks_both_overlapping_slots_and_is_not_regenerated(self):
        self.fixed()
        regular = self.lesson()
        movable_pd = self.lesson(group=None, project=self.project, lesson_type=LessonType.PD)
        result = self.generate()
        self.assertEqual((result.status, result.scheduled_count, result.fixed_pd_count), ("SOLVED", 1, 1))
        placed = ScheduledClass.objects.get(run_id=result.run_id)
        self.assertEqual(placed.event_id, regular.pk)
        self.assertIn(placed.slot.lesson_number, (3, 4))
        self.assertFalse(ScheduledClass.objects.filter(run_id=result.run_id, event=movable_pd).exists())
        snapshot = GenerationRunSerializer(GenerationRun.objects.get(pk=result.run_id)).data["fixed_pd_snapshot"]
        self.assertEqual((snapshot[0]["start"], snapshot[0]["end"], snapshot[0]["room_id"]), ("13:00", "16:00", None))
        self.assertEqual(snapshot[0]["student_ids"], [self.student.pk])

    def test_default_day_rules_keep_monday_pd_and_place_regular_pair_on_tuesday(self):
        self.day_rules.stop()
        self.fixed()
        self.lesson()
        TimeSlot.objects.create(week_pattern=self.pattern, day_of_week=1,
                                lesson_number=1, start_time=time(12, 20), end_time=time(13, 50))
        result = self.generate()
        self.assertEqual((result.status, result.fixed_pd_count), ("SOLVED", 1))
        self.assertEqual(ScheduledClass.objects.get(run_id=result.run_id).slot.day_of_week, 1)

    def test_unrelated_group_can_use_same_slot_and_university_room(self):
        self.only_slots(2)
        self.fixed()
        self.lesson(group=self.other_group)
        result = self.generate()
        self.assertEqual(result.status, "SOLVED")
        self.assertIsNotNone(ScheduledClass.objects.get(run_id=result.run_id).room_id)

    def test_same_teacher_is_busy_even_with_unrelated_group(self):
        self.only_slots(2)
        self.fixed()
        self.lesson(group=self.other_group, teacher=self.teacher)
        result = self.generate()
        self.assertEqual((result.status, result.scheduled_count, result.issue_count), ("PARTIAL", 0, 1))

    def test_lecture_stream_including_participant_group_is_blocked(self):
        self.only_slots(2)
        self.fixed()
        stream = LectureStream.objects.create(semester=self.semester, subject=self.subject)
        stream.groups.set([self.group, self.other_group])
        self.lesson(group=None, stream=stream, lesson_type=LessonType.LECTURE)
        self.assertEqual(self.generate().status, "PARTIAL")

    def test_fixed_university_room_is_reserved_for_other_people(self):
        self.only_slots(2)
        self.fixed(room=self.room, place_details={"kind": "rooms", "raw": "Н106"})
        self.lesson(group=self.other_group)
        result = self.generate()
        self.assertEqual(result.status, "SOLVED")
        self.assertEqual(ScheduledClass.objects.get(run_id=result.run_id).room_id, self.other_room.pk)

    def test_travel_buffer_blocks_nearby_onsite_pair_but_not_later_pair(self):
        self.only_slots(3, 4)
        self.fixed(travel_buffer_minutes=30)
        self.lesson()
        result = self.generate()
        self.assertEqual(result.status, "SOLVED")
        self.assertEqual(ScheduledClass.objects.get(run_id=result.run_id).slot.lesson_number, 4)

    def test_travel_buffer_applies_before_pd_too(self):
        self.pattern.slots.all().delete()
        TimeSlot.objects.create(week_pattern=self.pattern, day_of_week=0, lesson_number=1,
                                start_time=time(12), end_time=time(12, 40))
        self.fixed(travel_buffer_minutes=30)
        self.lesson()
        self.assertEqual(self.generate().status, "PARTIAL")

    def test_online_class_does_not_require_travel_but_cannot_overlap(self):
        self.only_slots(3)
        self.fixed(travel_buffer_minutes=30)
        stream = LectureStream.objects.create(semester=self.semester, subject=self.subject)
        stream.groups.add(self.group)
        self.lesson(group=None, stream=stream, lesson_type=LessonType.LECTURE)
        self.assertEqual(self.generate().status, "SOLVED")

    def test_same_known_room_does_not_require_travel(self):
        self.only_slots(3)
        self.fixed(travel_buffer_minutes=30, room=self.room, place_details={"raw": "Н106"})
        self.lesson()
        result = self.generate()
        self.assertEqual(result.status, "SOLVED")
        self.assertEqual(ScheduledClass.objects.get(run_id=result.run_id).room_id, self.room.pk)

    def test_dated_pd_outside_module_does_not_block_or_get_regenerated(self):
        self.only_slots(2)
        self.fixed(date=date(2026, 11, 2))
        self.lesson()
        self.lesson(group=None, project=self.project, lesson_type=LessonType.PD)
        result = self.generate()
        self.assertEqual((result.status, result.scheduled_count, result.fixed_pd_count), ("SOLVED", 1, 0))

    def test_dated_sessions_on_different_weeks_are_not_a_fixed_conflict(self):
        self.only_slots(2)
        self.fixed(date=date(2026, 9, 14))
        self.fixed(date=date(2026, 9, 21), source_key="manual:second")
        self.lesson()
        result = self.generate()
        self.assertEqual((result.status, result.fixed_pd_count), ("PARTIAL", 2))

    def test_fixed_conflict_fails_without_destroying_previous_schedule(self):
        self.lesson()
        first = self.generate()
        self.fixed()
        self.fixed(source_key="manual:second")
        with self.assertRaisesMessage(GenerationError, "Конфликт фиксированных"):
            self.generate()
        self.assertEqual(GenerationRun.objects.latest("pk").status, "ERROR")
        self.assertTrue(ScheduledClass.objects.filter(run_id=first.run_id).exists())

    def test_missing_slot_times_fail_instead_of_ignoring_pd(self):
        self.fixed()
        self.lesson()
        self.pattern.slots.update(start_time=None)
        with self.assertRaisesMessage(GenerationError, "точностью до минуты"):
            self.generate()
        self.assertEqual(GenerationRun.objects.latest("pk").status, "ERROR")

    def test_partial_with_pd_keeps_previous_schedule(self):
        self.only_slots(2)
        self.lesson()
        first = self.generate()
        self.fixed()
        second = self.generate()
        self.assertEqual(second.status, "PARTIAL")
        self.assertTrue(ScheduledClass.objects.filter(run_id=first.run_id).exists())

    def test_unknown_membership_and_teacher_fail_explicitly(self):
        self.fixed()
        self.project.enrollments.all().delete()
        with self.assertRaisesMessage(GenerationError, "студент → проект"):
            self.generate()
        ProjectEnrollment.objects.create(project=self.project, student=self.student)
        self.project.supervisor = None
        self.project.save()
        with self.assertRaisesMessage(GenerationError, "преподаватель"):
            self.generate()

    def test_legacy_pd_cannot_overlap_practice_of_its_students_group(self):
        self.only_slots(2)
        self.lesson()
        self.lesson(group=None, project=self.project, teacher=self.teacher, lesson_type=LessonType.PD)
        result = self.generate()
        self.assertEqual((result.status, result.scheduled_count), ("PARTIAL", 1))

    def test_disjoint_projects_from_same_group_can_run_at_once(self):
        self.only_slots(2)
        student = Student.objects.create(group=self.group, full_name="Other participant")
        project = Project.objects.create(semester=self.semester, title="Other project")
        ProjectEnrollment.objects.create(project=project, student=student)
        self.lesson(group=None, project=self.project, teacher=self.teacher, lesson_type=LessonType.PD)
        self.lesson(group=None, project=project, lesson_type=LessonType.PD)
        result = self.generate()
        self.assertEqual((result.status, result.scheduled_count), ("SOLVED", 2))

    def test_import_updates_without_duplicates_and_keeps_manual_rows(self):
        self.fixed()
        first = self.import_document()
        document = self.document()
        document["projects"][0]["schedules"][0]["meetings"][0]["end"] = "15:40"
        second = self.import_document(document)
        self.assertEqual((first["created"], second["created"], second["updated"]), (1, 0, 1))
        self.assertEqual(FixedProjectSession.objects.count(), 2)
        imported = FixedProjectSession.objects.get(source__provider="google_pd_sheet")
        self.assertEqual(imported.end_time, time(15, 40))
        self.assertEqual(imported.source["cell"], {"a1": "G11"})

    def test_import_rejects_unreviewed_or_changed_source_without_mutation(self):
        for key, value in (("reviewed", False), ("source_sha256", "b" * 64)):
            mapping = self.mapping()
            mapping[key] = value
            with self.subTest(key=key), self.assertRaises(PDImportError):
                self.import_document(mapping=mapping)
        self.assertFalse(FixedProjectSession.objects.exists())

    def test_import_rejects_unresolved_notes_instead_of_losing_them(self):
        document = self.document()
        document["projects"][0]["schedules"][0]["notes"] = [{"text": "28.09 перенос на 12.10"}]
        with self.assertRaisesMessage(PDImportError, "сначала разберите"):
            self.import_document(document)
        self.assertFalse(FixedProjectSession.objects.exists())

    def test_imported_partner_never_reserves_university_room(self):
        mapping = self.mapping()
        mapping["projects"][0]["rooms"] = {self.place["raw"]: self.room.pk}
        with self.assertRaisesMessage(PDImportError, "не резервирует"):
            self.import_document(mapping=mapping)

    def test_import_university_room_requires_explicit_mapping(self):
        document = self.document()
        meeting = document["projects"][0]["schedules"][0]["meetings"][0]
        meeting["place_details"] = {"kind": "rooms", "raw": "Н106", "venue": None,
                                    "room_candidates": [{"label": "Н106"}]}
        with self.assertRaisesMessage(PDImportError, "сопоставление помещения"):
            self.import_document(document)
        mapping = self.mapping()
        mapping["projects"][0]["rooms"] = {"Н106": self.room.pk}
        self.import_document(document, mapping)
        self.assertEqual(FixedProjectSession.objects.get().room_id, self.room.pk)

    def test_failed_reimport_preserves_previous_data(self):
        self.import_document()
        document = self.document()
        meetings = document["projects"][0]["schedules"][0]["meetings"]
        meetings[0]["end"] = "15:40"
        bad = deepcopy(meetings[0])
        bad["start"] = "25:00"
        meetings.append(bad)
        with self.assertRaises(ValueError):
            self.import_document(document)
        self.assertEqual(FixedProjectSession.objects.get().end_time, time(16))

    def test_model_rejects_foreign_module_and_partner_room(self):
        session = self.fixed(room=self.room)
        with self.assertRaises(ValidationError):
            session.full_clean()
        session.room = None
        other = Semester.objects.create(name="Other semester", start_date=date(2026, 9, 1))
        session.module = Module.objects.create(semester=other, number=1, start_date=date(2026, 9, 1),
                                               end_date=date(2026, 10, 31), teaching_weeks=9)
        with self.assertRaises(ValidationError):
            session.full_clean()

    def test_api_generation_automatically_includes_fixed_pd(self):
        self.fixed()
        self.lesson()
        response = self.client.post("/api/schedule/generate/", data={
            "semester": self.semester.pk, "module": self.module.pk, "time_limit_seconds": 2},
            content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.json()["fixed_pd_snapshot"]), 1)
        slot = ScheduledClass.objects.get(run_id=response.json()["id"]).slot
        self.assertIn(slot.lesson_number, (3, 4))

    def test_import_then_api_generation_returns_classes_and_fixed_pd_in_one_run(self):
        self.import_document()
        self.lesson()
        response = self.client.post("/api/schedule/generate/", data={
            "semester": self.semester.pk, "module": self.module.pk,
            "time_limit_seconds": 2, "clear_previous": False}, content_type="application/json")
        self.assertEqual(response.status_code, 201)
        result = response.json()
        self.assertEqual(result["status"], "SOLVED")
        self.assertEqual(len(result["scheduled_classes"]), 1)
        self.assertEqual(len(result["fixed_pd_snapshot"]), 1)
        fixed = result["fixed_pd_snapshot"][0]
        self.assertEqual(fixed["student_ids"], [self.student.pk])
        self.assertEqual(fixed["source"]["sha256"], self.document()["source"]["sha256"])
        # ПД заканчивается в 16:00, запас 20 минут исключает слот 16:10.
        slot = ScheduledClass.objects.get(run_id=result["id"]).slot
        self.assertEqual(slot.lesson_number, 4)
        self.assertFalse(GenerationRun.objects.get(pk=result["id"]).issues.exists())

    def test_pattern_parity_filters_dated_pd_from_first_semester_monday(self):
        self.only_slots(2)
        self.fixed(date=date(2026, 9, 14))  # третья неделя от понедельника 31 августа
        self.lesson()
        self.pattern.parity = 2
        self.pattern.save()
        even = self.generate()
        self.assertEqual((even.status, even.fixed_pd_count), ("SOLVED", 0))
        self.pattern.parity = 1
        self.pattern.save()
        odd = self.generate()
        self.assertEqual((odd.status, odd.fixed_pd_count), ("PARTIAL", 1))

    def test_exact_end_boundary_does_not_block_next_pair(self):
        self.only_slots(3)
        self.pattern.slots.update(start_time=time(16))
        self.fixed()
        self.lesson()
        self.assertEqual(self.generate().status, "SOLVED")

    def test_legacy_pd_uses_project_supervisor_if_event_teacher_is_missing(self):
        self.only_slots(2)
        self.lesson(group=self.other_group, teacher=self.teacher)
        self.lesson(group=None, teacher=None, project=self.project, lesson_type=LessonType.PD)
        self.assertEqual(self.generate().scheduled_count, 1)

    def test_run_keeps_fixed_snapshot_after_source_changes(self):
        session = self.fixed()
        first = self.generate()
        session.end_time = time(17)
        session.save()
        snapshot = GenerationRun.objects.get(pk=first.run_id).fixed_pd_snapshot
        self.assertEqual(snapshot[0]["end"], "16:00")

    def test_import_removes_stale_imported_intervals_but_keeps_manual_session(self):
        self.fixed()
        document = self.document()
        meetings = document["projects"][0]["schedules"][0]["meetings"]
        second = deepcopy(meetings[0])
        second.update(start="16:10", end="17:40")
        meetings.append(second)
        self.import_document(document)
        result = self.import_document()
        self.assertEqual(result["removed"], 1)
        self.assertEqual(FixedProjectSession.objects.count(), 2)
        self.assertTrue(FixedProjectSession.objects.filter(source_key="manual:test").exists())

    def test_import_does_not_overwrite_manual_session_with_colliding_key(self):
        session = self.fixed(source_key=f"pd:{self.module.pk}:6:G11:0:0", end_time=time(15))
        with self.assertRaisesMessage(PDImportError, "ручной записью"):
            self.import_document()
        session.refresh_from_db()
        self.assertEqual(session.end_time, time(15))

    def test_shared_source_cell_in_two_columns_does_not_overwrite_interval(self):
        document = self.document()
        schedules = document["projects"][0]["schedules"]
        schedules.append(deepcopy(schedules[0]))
        schedules[1]["meetings"][0]["day_of_week"] = 2
        self.import_document(document)
        self.assertEqual(set(FixedProjectSession.objects.values_list("day_of_week", flat=True)), {0, 2})

    def test_dated_import_preserves_date_and_time(self):
        document = self.document()
        meeting = document["projects"][0]["schedules"][0]["meetings"][0]
        meeting.update(recurrence="dated", date="2026-10-12")
        self.import_document(document)
        self.assertEqual(FixedProjectSession.objects.get().date, date(2026, 10, 12))
        self.only_slots(2)
        self.lesson()
        result = self.generate()
        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(GenerationRun.objects.get(pk=result.run_id).fixed_pd_snapshot[0]["occurrence_dates"],
                         ["2026-10-12"])
