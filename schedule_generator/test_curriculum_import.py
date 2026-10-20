import io
import json
import tempfile
from datetime import date, time
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from schedule_generator.enums import LessonType
from schedule_generator.models import (
    Curriculum,
    CurriculumEntry,
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
from schedule_generator.services.curriculum import (
    CurriculumDataError,
    import_curriculum,
    prepare_module_load,
)
from schedule_generator.services.generation import GenerationError, run_generation


class CurriculumFixture:
    def document(self):
        return {
            "schema_version": 1,
            "source": {"sha256": "a" * 64, "filename": "synthetic-plan.pdf"},
            "metadata": {
                "program_code": "09.03.01",
                "profile_name": "Веб-технологии",
                "admission_year": 2024,
                "study_form_raw": "Очная",
            },
            "entries": [
                {
                    "source_row": 0,
                    "discipline_code": "Б1.1.1",
                    "subject": "Алгоритмы",
                    "totals": {"independent_hours": 36},
                    "controls": {
                        "exam_semesters": [5],
                        "credit_semesters": [],
                        "graded_credit_semesters": [],
                    },
                    "semester_loads": [
                        {
                            "semester_number": 5,
                            "contact_hours": 72,
                            "lecture_hours": 36,
                            "practice_hours": 18,
                            "lab_hours": 18,
                        }
                    ],
                    "raw_cells": {"subject": "Алгоритмы"},
                    "warnings": [],
                    "review_required": True,
                }
            ],
        }

    def review(self):
        return {
            "reviewed": True,
            "source_sha256": "a" * 64,
            "duration_months": 48,
            "entries": [
                {"source_row": 0, "semester_number": 5, "activity_kind": "REGULAR"}
            ],
        }

    def imported(self, document=None, review=None):
        return import_curriculum(
            document=document or self.document(), review=review or self.review()
        )


class CurriculumImportTests(CurriculumFixture, TestCase):
    def test_import_creates_plan_subject_and_semester_hours(self):
        result = self.imported()
        entry = CurriculumEntry.objects.get(pk=result["entry_ids"][0])
        self.assertEqual(
            (entry.curriculum.admission_year, entry.curriculum.study_form),
            (2024, "FULL_TIME"),
        )
        self.assertEqual(
            (entry.contact_hours, entry.lecture_hours, entry.independent_hours),
            (72, 36, 36),
        )
        self.assertTrue(entry.has_exam)
        self.assertFalse(entry.has_credit)
        self.assertEqual(entry.source["raw_cells"], {"subject": "Алгоритмы"})

    def test_reimport_updates_without_duplicates_and_corrects_source_code(self):
        first = self.imported()
        document = self.document()
        document["entries"][0]["discipline_code"] = "Б1.1.2"
        second = self.imported(document)
        self.assertEqual(second["entry_ids"], first["entry_ids"])
        self.assertEqual((second["created"], second["updated"]), (0, 1))
        self.assertEqual(
            (
                Curriculum.objects.count(),
                Subject.objects.count(),
                CurriculumEntry.objects.count(),
            ),
            (1, 1, 1),
        )

    def test_multisemester_unknown_hours_and_controls_are_not_zero(self):
        document = self.document()
        row = document["entries"][0]
        row["semester_loads"][0].update(
            lecture_hours=None, practice_hours=None, lab_hours=None
        )
        row["semester_loads"].append({"semester_number": 6, "contact_hours": 36})
        row["controls"]["exam_semesters"] = None
        self.imported(document)
        entry = CurriculumEntry.objects.get()
        self.assertIsNone(entry.lecture_hours)
        self.assertIsNone(entry.independent_hours)
        self.assertIsNone(entry.has_exam)
        self.assertEqual(entry.source["totals"]["independent_hours"], 36)

    def test_explicit_overrides_resolve_unknown_hours(self):
        document = self.document()
        document["entries"][0]["semester_loads"][0]["lecture_hours"] = None
        review = self.review()
        review["entries"][0]["overrides"] = {"lecture_hours": 36}
        self.imported(document, review)
        self.assertEqual(CurriculumEntry.objects.get().lecture_hours, 36)

    def test_review_and_source_hash_are_required_without_db_mutation(self):
        for key, value in (("reviewed", False), ("source_sha256", "b" * 64)):
            review = self.review()
            review[key] = value
            with self.subTest(key=key), self.assertRaises(CurriculumDataError):
                self.imported(review=review)
        self.assertFalse(Curriculum.objects.exists())

    def test_late_invalid_selection_rolls_back_subjects_and_plan(self):
        review = self.review()
        review["entries"].append(
            {"source_row": 99, "semester_number": 5, "activity_kind": "REGULAR"}
        )
        with self.assertRaises(CurriculumDataError):
            self.imported(review=review)
        self.assertFalse(Subject.objects.exists())
        self.assertFalse(Curriculum.objects.exists())

    def test_invalid_hour_breakdown_is_not_imported(self):
        review = self.review()
        review["entries"][0]["overrides"] = {"lecture_hours": 100}
        with self.assertRaisesMessage(CurriculumDataError, "сумма часов"):
            self.imported(review=review)

    def test_duplicate_selection_is_rejected(self):
        review = self.review()
        review["entries"] *= 2
        with self.assertRaises(CurriculumDataError):
            self.imported(review=review)
        self.assertFalse(CurriculumEntry.objects.exists())

    def test_known_hours_cannot_exceed_contact_total_even_with_unknown_breakdown(self):
        document = self.document()
        document["entries"][0]["semester_loads"][0].update(
            contact_hours=18, lab_hours=None
        )
        with self.assertRaisesMessage(CurriculumDataError, "сумма часов"):
            self.imported(document)
        self.assertFalse(CurriculumEntry.objects.exists())

    def test_manual_entry_is_preserved_and_metadata_rollback_is_atomic(self):
        result = self.imported()
        entry = CurriculumEntry.objects.get()
        entry.source = {}
        entry.save()
        with self.assertRaisesMessage(CurriculumDataError, "вручную"):
            self.imported()
        self.assertEqual(CurriculumEntry.objects.get().pk, result["entry_ids"][0])

    def test_same_named_subjects_require_explicit_id(self):
        Subject.objects.bulk_create(
            [Subject(name="Алгоритмы"), Subject(name="Алгоритмы")]
        )
        with self.assertRaisesMessage(CurriculumDataError, "subject_id"):
            self.imported()
        review = self.review()
        review["entries"][0]["subject_id"] = Subject.objects.first().pk
        self.imported(review=review)

    def test_import_command_calls_same_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            document, review = (
                Path(temporary) / "document.json",
                Path(temporary) / "review.json",
            )
            document.write_text(json.dumps(self.document()))
            review.write_text(json.dumps(self.review()))
            output = io.StringIO()
            call_command(
                "import_curriculum", str(document), review=str(review), stdout=output
            )
        self.assertEqual(json.loads(output.getvalue())["created"], 1)

    def test_reimport_cannot_shorten_plan_past_unselected_existing_entry(self):
        self.imported()
        review = self.review()
        review["duration_months"] = 24
        with self.assertRaisesMessage(CurriculumDataError, "Срок обучения"):
            self.imported(review=review)
        self.assertEqual(Curriculum.objects.get().duration_months, 48)


class ModuleLoadTests(CurriculumFixture, TestCase):
    def setUp(self):
        result = self.imported()
        self.plan = Curriculum.objects.get(pk=result["curriculum_id"])
        self.entry = CurriculumEntry.objects.get(pk=result["entry_ids"][0])
        self.semester = Semester.objects.create(
            name="2026 autumn", start_date=date(2026, 9, 1)
        )
        self.module = Module.objects.create(
            semester=self.semester,
            number=1,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 10, 31),
            teaching_weeks=9,
        )
        self.second = Module.objects.create(
            semester=self.semester,
            number=2,
            start_date=date(2026, 11, 1),
            end_date=date(2026, 12, 31),
            teaching_weeks=9,
        )
        self.group = Group.objects.create(
            name="241-3210", curriculum=self.plan, semester=self.semester
        )
        Student.objects.create(group=self.group, full_name="Вымышленный студент")
        self.teacher = Teacher.objects.create(full_name="Вымышленный преподаватель")

    def allocation(self, **overrides):
        value = {
            "curriculum_entry_id": self.entry.pk,
            "lesson_type": LessonType.LECTURE,
            "academic_hours": 18,
            "teacher_id": self.teacher.pk,
        }
        value.update(overrides)
        return value

    def prepare(self, entries=None, **options):
        return prepare_module_load(
            group=self.group,
            module=options.pop("module", self.module),
            semester_number=5,
            entries=entries or [self.allocation()],
            **options,
        )

    def test_preparation_uses_stream_and_repeats_without_duplicates(self):
        first = self.prepare()
        second = self.prepare()
        event = LessonEvent.objects.get()
        self.assertEqual(first["event_ids"], second["event_ids"])
        self.assertIsNone(event.group_id)
        self.assertEqual(list(event.stream.groups.all()), [self.group])
        self.assertEqual(
            (event.pairs_per_week, event.academic_hours, LectureStream.objects.count()),
            (1, 18, 1),
        )

    def test_module_budgets_are_summed_across_modules(self):
        self.prepare()
        self.prepare(module=self.second)
        with self.assertRaisesMessage(CurriculumDataError, "распределено"):
            self.prepare([self.allocation(academic_hours=36)], module=self.second)
        self.assertEqual(
            list(LessonEvent.objects.values_list("academic_hours", flat=True)), [18, 18]
        )

    def test_each_group_has_own_semester_budget(self):
        self.prepare([self.allocation(academic_hours=36)])
        other = Group.objects.create(name="241-3211", curriculum=self.plan)
        Student.objects.create(group=other, full_name="Вымышленный студент 2")
        result = prepare_module_load(
            group=other,
            module=self.module,
            semester_number=5,
            entries=[self.allocation(academic_hours=36)],
        )
        self.assertEqual(result["created"], 1)

    def test_unknown_and_indivisible_hours_fail_without_rounding(self):
        with self.assertRaisesMessage(CurriculumDataError, "не делятся"):
            self.prepare([self.allocation(academic_hours=17)])
        self.entry.lecture_hours = None
        self.entry.save()
        with self.assertRaisesMessage(CurriculumDataError, "в плане None"):
            self.prepare()
        self.assertFalse(LessonEvent.objects.exists())
        self.assertFalse(LectureStream.objects.exists())

    def test_group_admission_year_must_match_plan(self):
        self.plan.admission_year = 2025
        self.plan.save()
        with self.assertRaisesMessage(CurriculumDataError, "Год набора"):
            self.prepare()

    def test_project_and_pe_rows_do_not_create_group_lessons(self):
        for kind in ("PD", "PE"):
            self.entry.source["activity_kind"] = kind
            self.entry.save()
            with (
                self.subTest(kind=kind),
                self.assertRaisesMessage(CurriculumDataError, "ПД/физкультура"),
            ):
                self.prepare()

    def test_manual_lesson_is_not_duplicated(self):
        manual = LessonEvent.objects.create(
            semester=self.semester,
            module=self.module,
            subject=self.entry.subject,
            group=self.group,
            lesson_type=LessonType.PRACTICE,
        )
        with self.assertRaisesMessage(CurriculumDataError, "вне этой подготовки"):
            self.prepare([self.allocation(lesson_type=LessonType.PRACTICE)])
        self.assertEqual(LessonEvent.objects.get().pk, manual.pk)

    def test_replace_deletes_only_owned_unscheduled_load(self):
        self.prepare()
        manual = LessonEvent.objects.create(
            semester=self.semester,
            subject=Subject.objects.create(name="Кружок"),
            group=self.group,
            lesson_type=LessonType.EXTRACURRICULAR,
        )
        result = self.prepare(
            [self.allocation(lesson_type=LessonType.PRACTICE)], replace=True
        )
        self.assertEqual(result["removed"], 1)
        self.assertTrue(LessonEvent.objects.filter(pk=manual.pk).exists())

    def test_reimport_cannot_shrink_hours_below_allocated_load(self):
        self.prepare([self.allocation(academic_hours=36)])
        document = self.document()
        document["entries"][0]["semester_loads"][0].update(
            contact_hours=54, lecture_hours=18
        )
        with self.assertRaisesMessage(CurriculumDataError, "распределено"):
            self.imported(document)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.lecture_hours, 36)

    def grid(self):
        pattern = WeekPattern.objects.create(semester=self.semester)
        for day in range(6):
            for number, hour in enumerate((9, 11, 13, 15), 1):
                TimeSlot.objects.create(
                    week_pattern=pattern,
                    day_of_week=day,
                    lesson_number=number,
                    start_time=time(hour),
                    end_time=time(hour + 1, 30),
                )
        Room.objects.create(name="Тестовая аудитория", capacity=30)

    def test_scheduled_load_is_repeatable_but_history_cannot_be_rewritten(self):
        self.prepare()
        self.grid()
        first = run_generation(
            semester=self.semester, module=self.module, time_limit_seconds=2
        )
        self.prepare()
        with self.assertRaisesMessage(CurriculumDataError, "историю"):
            self.prepare([self.allocation(academic_hours=36)])
        self.assertEqual(ScheduledClass.objects.filter(run_id=first.run_id).count(), 1)

    def test_changed_module_calendar_requires_preparation_before_generation(self):
        self.prepare()
        self.grid()
        first = run_generation(
            semester=self.semester, module=self.module, time_limit_seconds=2
        )
        self.module.teaching_weeks = 8
        self.module.save()
        with self.assertRaisesMessage(GenerationError, "prepare-load"):
            run_generation(
                semester=self.semester, module=self.module, time_limit_seconds=2
            )
        self.assertEqual(ScheduledClass.objects.filter(run_id=first.run_id).count(), 1)
        self.assertEqual(GenerationRun.objects.order_by("pk").last().status, "ERROR")

    def test_replace_rolls_back_if_stale_load_is_already_scheduled(self):
        self.prepare()
        self.grid()
        run_generation(semester=self.semester, module=self.module, time_limit_seconds=2)
        with self.assertRaisesMessage(CurriculumDataError, "историю"):
            self.prepare(
                [self.allocation(lesson_type=LessonType.PRACTICE)], replace=True
            )
        self.assertEqual(LessonEvent.objects.get().lesson_type, LessonType.LECTURE)

    def test_prepare_command_returns_event_ids_and_reuses_load(self):
        with tempfile.TemporaryDirectory() as temporary:
            allocation = Path(temporary) / "allocation.json"
            allocation.write_text(
                json.dumps({"semester_number": 5, "entries": [self.allocation()]})
            )
            output = io.StringIO()
            call_command(
                "prepare_module_load",
                str(allocation),
                group=self.group.pk,
                module=self.module.pk,
                stdout=output,
            )
        self.assertEqual(
            json.loads(output.getvalue())["event_ids"], self.prepare()["event_ids"]
        )

    def test_end_to_end_30_students_different_pd_and_two_lectures(self):
        Student.objects.bulk_create(
            [
                Student(group=self.group, full_name=f"Вымышленный студент {i}")
                for i in range(2, 31)
            ]
        )
        self.grid()
        students = list(self.group.students.order_by("pk"))
        for index in range(6):
            members = students[index * 5 : (index + 1) * 5]
            teacher = Teacher.objects.create(
                full_name=f"Руководитель тестового ПД {index}"
            )
            project = Project.objects.create(
                semester=self.semester, title=f"Тестовое ПД {index}", supervisor=teacher
            )
            ProjectEnrollment.objects.bulk_create(
                [ProjectEnrollment(student=s, project=project) for s in members]
            )
            FixedProjectSession.objects.create(
                project=project,
                module=self.module,
                day_of_week=index,
                start_time=time(9),
                end_time=time(10, 30),
                source_key="test",
                travel_buffer_minutes=30,
                place_details={
                    "raw": "Тестовое дополнительное место",
                    "venue": {"kind": "additional"},
                },
            )
        self.prepare(
            [
                self.allocation(academic_hours=36),
                self.allocation(lesson_type=LessonType.PRACTICE),
                self.allocation(lesson_type=LessonType.LAB),
            ]
        )
        result = run_generation(
            semester=self.semester, module=self.module, time_limit_seconds=3
        )
        self.assertEqual(
            (result.status, result.scheduled_count, result.fixed_pd_count),
            ("SOLVED", 4, 6),
        )
        run = GenerationRun.objects.get(pk=result.run_id)
        self.assertFalse(run.issues.exists())
        self.assertEqual(
            [len(s["student_ids"]) for s in run.fixed_pd_snapshot], [5] * 6
        )
        classes = list(run.scheduled_classes.select_related("event", "slot"))
        self.assertEqual(
            sum(c.event.lesson_type == LessonType.LECTURE for c in classes), 2
        )
        self.assertEqual(len({c.slot_id for c in classes}), 4)
        self.assertTrue(all(c.event.audience_size == 30 for c in classes))
        self.assertTrue(all(c.slot.start_time >= time(11) for c in classes))

    def test_http_import_and_prepare_require_staff_and_share_services(self):
        payload = {"document": self.document(), "review": self.review()}
        self.assertEqual(
            self.client.post(
                "/api/schedule/curricula/import/",
                payload,
                content_type="application/json",
            ).status_code,
            403,
        )
        admin = get_user_model().objects.create_superuser(
            username="admin", password="test-only"
        )
        self.client.force_login(admin)
        response = self.client.post(
            "/api/schedule/curricula/import/", payload, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            f"/api/schedule/groups/{self.group.pk}/prepare-load/",
            {
                "module": self.module.pk,
                "semester_number": 5,
                "entries": [self.allocation()],
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["created"], 1)
        response = self.client.post(
            f"/api/schedule/groups/{self.group.pk}/prepare-load/",
            {
                "module": self.module.pk,
                "semester_number": 5,
                "entries": [self.allocation(academic_hours=19)],
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(LessonEvent.objects.count(), 1)

    def test_http_rejects_malformed_import_and_direct_edit_of_prepared_event(self):
        admin = get_user_model().objects.create_superuser(
            username="admin", password="test-only"
        )
        self.client.force_login(admin)
        document = self.document()
        document["entries"][0]["semester_loads"] = [None]
        response = self.client.post(
            "/api/schedule/curricula/import/",
            {"document": document, "review": self.review()},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        event_id = self.prepare()["event_ids"][0]
        response = self.client.patch(
            f"/api/schedule/events/{event_id}/",
            {"pairs_per_week": 2},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(LessonEvent.objects.get(pk=event_id).pairs_per_week, 1)
