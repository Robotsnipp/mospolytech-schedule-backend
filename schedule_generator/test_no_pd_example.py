"""The current no-PD example stays isolated from historical synthetic projects."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.apps import apps
from django.test import TestCase

from schedule_generator.enums import LessonType
from schedule_generator.management.commands.seed_generation_example import seed_example
from schedule_generator.models import (
    Curriculum,
    FixedProjectSession,
    GenerationRun,
    Group,
    LessonEvent,
    Project,
    ProjectEnrollment,
    Student,
    Teacher,
    WeekPattern,
)
from schedule_generator.services.generation import run_generation
from schedule_generator.services.generation_audit import audit_run
from schedule_generator.test_generation_example import write_source

NO_PD_MARKER = "moscow-day-no-pd-v1"


class NoPDExampleTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        write_source(self.directory)

    def generate_both_modules(self, seeded, *, expected_pd):
        pattern = WeekPattern.objects.get(pk=seeded["week_pattern_id"])
        audits = []
        self.assertEqual(len(seeded["modules"]), 2)
        for item in seeded["modules"]:
            module = pattern.semester.modules.get(pk=item["module_id"])
            outcome = run_generation(
                semester=pattern.semester, module=module, week_pattern=pattern,
                clear_previous=False, seed=17, time_limit_seconds=5,
            )
            self.assertEqual(outcome.status, "SOLVED")
            run = GenerationRun.objects.get(pk=outcome.run_id)
            audit = audit_run(run)
            self.assertTrue(audit["passed"], audit)
            self.assertEqual((audit["required_hours"], audit["placed_hours"]), (18, 18))
            self.assertEqual(audit["fixed_pd_count"], expected_pd)
            self.assertEqual(len(run.fixed_pd_snapshot), expected_pd)
            self.assertFalse(run.scheduled_classes.filter(event__lesson_type=LessonType.PD).exists())
            audits.append(audit)
        return audits

    def historical_snapshot(self):
        # Include saved runs/results, all original inputs and M2M memberships.
        # Later creation may add rows, but no original column may change.
        return {
            model: list(model.objects.order_by(model._meta.pk.name).values())
            for model in apps.get_app_config("schedule_generator").get_models(include_auto_created=True)
        }

    def assert_history_unchanged(self, before):
        for model, rows in before.items():
            primary_key = model._meta.pk.name
            actual = list(model.objects.filter(**{
                f"{primary_key}__in": [row[primary_key] for row in rows],
            }).order_by(primary_key).values())
            self.assertEqual(actual, rows, model._meta.label)

    def test_default_example_generates_both_modules_without_mutating_historical_pd(self):
        legacy = seed_example(source_dir=self.directory, semester_name="Историческая ПД",
                              include_synthetic_pd=True)
        legacy_group = Group.objects.get(pk=legacy["group_id"])
        self.assertEqual(legacy_group.name, "251-3210")
        self.assertEqual(legacy["project_count"], 6)
        self.generate_both_modules(legacy, expected_pd=6)
        historical = self.historical_snapshot()

        fresh = seed_example(source_dir=self.directory, semester_name="Новый пример без ПД")
        group = Group.objects.select_related("curriculum").get(pk=fresh["group_id"])
        legacy_group.refresh_from_db()
        self.assertEqual(group.name, "251-3210 · демо без ПД")
        self.assertTrue(group.name.startswith("251-"))
        self.assertEqual(group.admission_year, 2025)
        group.full_clean()
        self.assertNotEqual(group.semester_id, legacy_group.semester_id)
        self.assertNotEqual(group.curriculum_id, legacy_group.curriculum_id)
        self.assertNotEqual(group.curriculum.version, legacy_group.curriculum.version)
        self.assertEqual(group.curriculum.source["example_seed"], NO_PD_MARKER)
        self.assertNotEqual(legacy_group.curriculum.source.get("example_seed"), NO_PD_MARKER)
        self.assertEqual(group.curriculum.entries.get().practice_hours, 36)
        self.assertEqual(legacy_group.curriculum.entries.get().practice_hours, 36)
        self.assertEqual(Student.objects.filter(group=group).count(), 30)
        self.assertEqual(Student.objects.filter(group=legacy_group).count(), 30)
        self.assertEqual(fresh["project_count"], 0)
        self.assertFalse(Project.objects.filter(semester_id=fresh["semester_id"]).exists())
        self.assertFalse(ProjectEnrollment.objects.filter(student__group=group).exists())
        self.assertFalse(FixedProjectSession.objects.filter(project__semester_id=fresh["semester_id"]).exists())
        events = LessonEvent.objects.filter(semester_id=fresh["semester_id"])
        self.assertEqual(events.count(), 2)
        self.assertEqual(set(events.values_list("lesson_type", flat=True)), {LessonType.PRACTICE})
        self.assertEqual(sorted(events.values_list("academic_hours", flat=True)), [18, 18])
        self.generate_both_modules(fresh, expected_pd=0)
        self.assert_history_unchanged(historical)

    def test_default_seed_needs_no_project_payload_and_creates_only_referenced_teachers(self):
        path = self.directory / "data.json"
        data = json.loads(path.read_text())
        del data["projects"]
        path.write_text(json.dumps(data), encoding="utf-8")
        result = seed_example(source_dir=self.directory, semester_name="Вход без проектов")
        self.assertEqual(result["project_count"], 0)
        self.assertEqual(Teacher.objects.count(), 1)
        teacher = Teacher.objects.get()
        self.assertIn("Тестовый преподаватель 1", teacher.full_name)
        self.assertEqual(set(LessonEvent.objects.values_list("teacher_id", flat=True)), {teacher.pk})
        self.assertFalse(Project.objects.exists())
        self.assertFalse(ProjectEnrollment.objects.exists())
        self.assertFalse(FixedProjectSession.objects.exists())
        self.generate_both_modules(result, expected_pd=0)

    def test_options_selects_current_no_pd_example_and_limits_its_teacher_catalog(self):
        legacy = seed_example(source_dir=self.directory, semester_name="Каталог с ПД",
                              include_synthetic_pd=True)
        current = seed_example(source_dir=self.directory, semester_name="Каталог без ПД")
        response = self.client.get("/api/schedule/generation-options/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["default_semester_id"], current["semester_id"])
        periods = {item["id"]: item for item in payload["semesters"]}
        self.assertIn(legacy["semester_id"], periods)
        ordinary_ids = set(LessonEvent.objects.filter(
            semester_id=current["semester_id"],
        ).values_list("teacher_id", flat=True))
        self.assertEqual(len(ordinary_ids), 1)
        current_teachers = {item["id"] for item in periods[current["semester_id"]]["teachers"]}
        self.assertEqual(current_teachers, ordinary_ids)
        historical_supervisors = set(Project.objects.filter(
            semester_id=legacy["semester_id"],
        ).values_list("supervisor_id", flat=True))
        self.assertTrue(current_teachers.isdisjoint(historical_supervisors))
        self.assertEqual(periods[current["semester_id"]]["groups"], [{
            "id": current["group_id"], "name": "251-3210 · демо без ПД", "student_count": 30,
        }])
        plan = Curriculum.objects.get(pk=current["curriculum_id"])
        self.assertEqual(plan.source["example_seed"], NO_PD_MARKER)
