"""Программы ДПО: планирование на реальном CP-SAT поверх основного расписания."""

from datetime import date, time

from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from schedule_generator.enums import Format, LessonType
from schedule_generator.models import (
    DPODiscipline,
    DPOMembership,
    DPOProgram,
    GenerationRun,
    Group,
    LessonEvent,
    Module,
    Room,
    ScheduledClass,
    Semester,
    Student,
    Subject,
    Teacher,
    TimeSlot,
    WeekPattern,
)
from schedule_generator.services.config import SolverConfig
from schedule_generator.services.dpo import (
    DPOPlanningError,
    plan_program,
    prepare_program_load,
)
from schedule_generator.services.dto import EventSpec, FixedPDSpec
from schedule_generator.services.generation import run_generation
from schedule_generator.services.generator import generate_schedule
from schedule_generator.services.schedule_queries import (
    resolve_run,
    scheduled_classes_for,
    student_holder_filter,
)

TIMES = ((1, (9, 0), (10, 30)), (2, (10, 40), (12, 10)), (3, (12, 20), (13, 50)), (4, (14, 30), (16, 0)))


class DPOScenario(TestCase):
    """Группа G1 (3 студента) с основным расписанием; двое из них - слушатели ДПО."""

    slots_count = 4

    def setUp(self):
        self.semester = Semester.objects.create(name="2026 autumn", start_date=date(2026, 9, 1))
        self.module = Module.objects.create(semester=self.semester, number=1, start_date=date(2026, 9, 1),
                                            end_date=date(2026, 9, 30), teaching_weeks=4)
        self.pattern = WeekPattern.objects.create(semester=self.semester)
        for number, start, end in TIMES[:self.slots_count]:
            TimeSlot.objects.create(week_pattern=self.pattern, day_of_week=0, lesson_number=number,
                                    start_time=time(*start), end_time=time(*end))
        self.room = Room.objects.create(name="Room 1", capacity=40)
        self.g1 = Group.objects.create(name="G1")
        self.g2 = Group.objects.create(name="G2")
        self.anna, self.boris, self.vera = (Student.objects.create(group=self.g1, full_name=name)
                                            for name in ("Anna", "Boris", "Vera"))
        self.gleb = Student.objects.create(group=self.g2, full_name="Gleb")
        self.main_teacher = Teacher.objects.create(full_name="Main teacher")
        self.dpo_teacher = Teacher.objects.create(full_name="DPO teacher")
        self.math = Subject.objects.create(name="Math")
        self.python = Subject.objects.create(name="Python")
        self.dpo_group = Group.objects.create(name="ДПО-Python", is_dpo=True)
        DPOMembership.objects.create(group=self.dpo_group, student=self.anna)
        DPOMembership.objects.create(group=self.dpo_group, student=self.boris)
        self.program = DPOProgram.objects.create(name="Python", semester=self.semester, module=self.module)
        self.program.groups.add(self.dpo_group)

    def main_lesson(self, group, pairs=1, teacher=None):
        return LessonEvent.objects.create(semester=self.semester, module=self.module, subject=self.math,
                                          group=group, teacher=teacher or self.main_teacher,
                                          lesson_type=LessonType.PRACTICE, pairs_per_week=pairs,
                                          audience_size=3)

    def main_run(self):
        outcome = run_generation(semester=self.semester, module=self.module, time_limit_seconds=5)
        self.assertEqual(outcome.status, "SOLVED")
        return GenerationRun.objects.get(pk=outcome.run_id)

    def discipline(self, **values):
        data = dict(program=self.program, subject=self.python, teacher=self.dpo_teacher,
                    format=Format.ONSITE, total_hours=8)  # 4 пары / 4 недели = 1 в неделю
        if "total_pairs" in values:
            data.pop("total_hours")
        data.update(values)
        return DPODiscipline.objects.create(**data)

    def plan(self):
        return plan_program(self.program, time_limit_seconds=5)

    def slots_of(self, run_id, **filters):
        return set(ScheduledClass.objects.filter(run_id=run_id, **filters)
                   .values_list("slot__lesson_number", flat=True))


class PlanningTests(DPOScenario):
    def test_dpo_avoids_members_main_classes_and_keeps_main_schedule(self):
        self.main_lesson(self.g1, pairs=2)
        base = self.main_run()
        base_before = sorted(base.scheduled_classes.values_list("event_id", "slot_id", "room_id"))
        self.discipline(total_hours=16)  # 2 пары в неделю
        result = self.plan()
        self.assertEqual((result["status"], result["scheduled_count"], result["required_count"]),
                         ("SOLVED", 2, 2))
        self.assertEqual(result["base_run_id"], base.pk)
        dpo_slots = self.slots_of(result["run_id"])
        self.assertEqual(len(dpo_slots), 2)
        self.assertFalse(dpo_slots & self.slots_of(base.pk, event__group=self.g1))
        # Основной запуск не изменился.
        self.assertEqual(sorted(base.scheduled_classes.values_list("event_id", "slot_id", "room_id")),
                         base_before)

    def test_teacher_busy_in_main_schedule_is_respected(self):
        # Основная пара G2 у того же преподавателя; слушатели из G1 свободны всегда.
        self.pattern.slots.filter(lesson_number__gt=2).delete()
        self.main_lesson(self.g2, teacher=self.dpo_teacher)
        base = self.main_run()
        self.discipline(format=Format.ONLINE)  # аудитория не мешает: проверяем только преподавателя
        result = self.plan()
        self.assertEqual(result["status"], "SOLVED")
        self.assertEqual(self.slots_of(result["run_id"]),
                         {1, 2} - self.slots_of(base.pk))

    def test_room_busy_in_main_schedule_is_respected(self):
        # Одна аудитория, два слота: пара G2 занимает аудиторию в одном из них.
        self.pattern.slots.filter(lesson_number__gt=2).delete()
        self.main_lesson(self.g2)
        base = self.main_run()
        self.discipline()
        result = self.plan()
        self.assertEqual(result["status"], "SOLVED")
        self.assertEqual(self.slots_of(result["run_id"]), {1, 2} - self.slots_of(base.pk))

    def test_non_members_do_not_block(self):
        # Единственный слот занят парой G1, но слушатель ДПО только Глеб из G2,
        # а пара ДПО онлайн (аудитория не нужна) и у другого преподавателя.
        self.pattern.slots.filter(lesson_number__gt=1).delete()
        DPOMembership.objects.filter(group=self.dpo_group).delete()
        DPOMembership.objects.create(group=self.dpo_group, student=self.gleb)
        self.main_lesson(self.g1)
        self.main_run()
        self.discipline(format=Format.ONLINE)
        self.assertEqual(self.plan()["status"], "SOLVED")

    def test_partial_when_members_have_no_free_slot(self):
        self.pattern.slots.filter(lesson_number__gt=2).delete()
        self.main_lesson(self.g1, pairs=2)
        base = self.main_run()
        self.discipline()
        result = self.plan()
        self.assertNotEqual(result["status"], "SOLVED")
        self.assertEqual(result["scheduled_count"], 0)
        run = GenerationRun.objects.get(pk=result["run_id"])
        self.assertTrue(run.issues.exists())
        self.assertEqual(base.scheduled_classes.count(), 2)

    def test_other_program_is_background(self):
        self.pattern.slots.filter(lesson_number__gt=2).delete()
        self.discipline()
        first = self.plan()
        other_group = Group.objects.create(name="ДПО-Data", is_dpo=True)
        DPOMembership.objects.create(group=other_group, student=self.anna)
        other = DPOProgram.objects.create(name="Data", semester=self.semester, module=self.module)
        other.groups.add(other_group)
        DPODiscipline.objects.create(program=other, subject=self.math,
                                     teacher=Teacher.objects.create(full_name="Data teacher"),
                                     format=Format.ONLINE, total_pairs=4)
        second = plan_program(other, time_limit_seconds=5)
        self.assertEqual(second["status"], "SOLVED")
        self.assertEqual(second["other_dpo_run_ids"], [first["run_id"]])
        self.assertFalse(self.slots_of(first["run_id"]) & self.slots_of(second["run_id"]))

    def test_main_generation_ignores_and_keeps_dpo_runs(self):
        self.main_lesson(self.g1)
        self.main_run()
        self.discipline()
        dpo = self.plan()
        # Повторная основная генерация с очисткой не трогает пары ДПО и не
        # планирует их сама; запуск по умолчанию - основной.
        rerun = self.main_run()
        self.assertEqual(ScheduledClass.objects.filter(run_id=dpo["run_id"]).count(), 1)
        self.assertFalse(rerun.scheduled_classes.filter(event__lesson_type=LessonType.DPO).exists())
        self.assertEqual(resolve_run(self.semester, self.module), rerun)

    def test_member_sees_dpo_classes_in_dpo_run(self):
        self.discipline()
        result = self.plan()
        classes = scheduled_classes_for(self.semester, student_holder_filter(self.anna),
                                        module=self.module, run_id=result["run_id"])
        self.assertEqual(classes.count(), 1)
        self.assertFalse(scheduled_classes_for(self.semester, student_holder_filter(self.vera),
                                               module=self.module, run_id=result["run_id"]).exists())


class FixedBusyWithoutTeacherTests(DPOScenario):
    def test_missing_teacher_on_both_sides_is_not_a_shared_person(self):
        # Занятие без преподавателя и неподвижная занятость без преподавателя
        # (пара основного расписания без назначенного преподавателя) в одном
        # слоте: общих людей нет, значит размещение разрешено.
        self.pattern.slots.filter(lesson_number__gt=1).delete()
        slot = self.pattern.slots.get()
        spec = EventSpec(event_id=1, lesson_type=LessonType.DPO, format=Format.ONLINE, pairs_per_week=1,
                         audience_size=1, teacher_id=None, blocking_student_ids={self.anna.pk})
        busy = FixedPDSpec(session_id=-1, project_id=0, day_of_week=0, start_minute=9 * 60,
                           end_minute=10 * 60 + 30, teacher_id=None, student_ids=frozenset({self.vera.pk}),
                           participant_group_ids=frozenset({self.g1.pk}), room_id=None, place_raw="",
                           travel_buffer_minutes=0, occurrence_dates=frozenset())
        result = generate_schedule(slots=[slot], rooms=[], specs=[spec], teachers_by_id={},
                                   config=SolverConfig(time_limit_seconds=5), fixed_pd=[busy])
        self.assertEqual(result.status, "SOLVED")
        self.assertEqual([a["slot_id"] for a in result.assignments], [slot.pk])


class PreparationTests(DPOScenario):
    def test_pairs_per_week_from_total(self):
        self.discipline(total_hours=16)
        self.discipline(subject=self.math, total_pairs=4, format=Format.ONLINE)
        events = prepare_program_load(self.program)
        self.assertEqual(sorted(e.pairs_per_week for e in events), [1, 2])
        self.assertEqual({e.audience_size for e in events}, {2})
        self.assertTrue(all(e.lesson_type == LessonType.DPO and e.group == self.dpo_group for e in events))
        # Повторная подготовка обновляет те же события.
        self.assertEqual({e.pk for e in prepare_program_load(self.program)}, {e.pk for e in events})

    def test_total_not_divisible_by_weeks_is_error(self):
        self.discipline(total_pairs=6)
        with self.assertRaisesMessage(DPOPlanningError, "не делятся на 4 недель"):
            prepare_program_load(self.program)

    def test_odd_hours_rejected(self):
        with self.assertRaises(ValidationError):
            self.discipline(total_hours=7).full_clean()

    def test_hours_or_pairs_required(self):
        with self.assertRaises(ValidationError):
            DPODiscipline(program=self.program, subject=self.python, teacher=self.dpo_teacher).full_clean()

    def test_group_without_members_is_error(self):
        empty = Group.objects.create(name="ДПО-Empty", is_dpo=True)
        self.program.groups.add(empty)
        self.discipline()
        with self.assertRaisesMessage(DPOPlanningError, "нет слушателей"):
            prepare_program_load(self.program)

    def test_weeks_required_without_module(self):
        self.program.module = None
        self.program.save()
        self.discipline()
        with self.assertRaisesMessage(DPOPlanningError, "недель"):
            prepare_program_load(self.program)

    def test_removed_discipline_with_classes_is_protected(self):
        discipline = self.discipline()
        self.plan()
        discipline.delete()
        self.discipline(subject=self.math)
        with self.assertRaisesMessage(DPOPlanningError, "уже есть в запусках"):
            prepare_program_load(self.program)

    def test_membership_requires_dpo_group_and_main_student(self):
        with self.assertRaises(ValidationError):
            DPOMembership(group=self.g2, student=self.anna).full_clean()
        legacy = Student.objects.create(group=self.dpo_group, full_name="Direct")
        with self.assertRaises(ValidationError):
            DPOMembership(group=self.dpo_group, student=legacy).full_clean()


class ApiTests(DPOScenario):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        self.client = APIClient()
        self.admin = get_user_model().objects.create_user("admin", password="x", is_staff=True)

    def test_plan_requires_admin_and_returns_run(self):
        self.discipline()
        url = f"/api/schedule/dpo-programs/{self.program.pk}/plan/"
        self.assertIn(self.client.post(url, {}, format="json").status_code, (401, 403))
        self.client.force_authenticate(self.admin)
        response = self.client.post(url, {"time_limit_seconds": 5}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["status"], "SOLVED")
        runs = self.client.get(f"/api/schedule/dpo-programs/{self.program.pk}/runs/").json()
        self.assertEqual([run["id"] for run in runs], [response.json()["run_id"]])

    def test_plan_error_is_400(self):
        self.discipline(total_pairs=6)
        self.client.force_authenticate(self.admin)
        response = self.client.post(f"/api/schedule/dpo-programs/{self.program.pk}/plan/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("не делятся", response.json()["detail"])

    def test_crud_programs_disciplines_members(self):
        self.client.force_authenticate(self.admin)
        program = self.client.post("/api/schedule/dpo-programs/", {
            "name": "New", "semester": self.semester.pk, "module": self.module.pk,
            "groups": [self.dpo_group.pk]}, format="json")
        self.assertEqual(program.status_code, 201, program.content)
        discipline = self.client.post("/api/schedule/dpo-disciplines/", {
            "program": program.json()["id"], "subject": self.python.pk, "teacher": self.dpo_teacher.pk,
            "format": "ONLINE", "total_hours": 8}, format="json")
        self.assertEqual(discipline.status_code, 201, discipline.content)
        bad = self.client.post("/api/schedule/dpo-disciplines/", {
            "program": program.json()["id"], "subject": self.math.pk, "teacher": self.dpo_teacher.pk,
            "total_hours": 7}, format="json")
        self.assertEqual(bad.status_code, 400)
        member = self.client.post("/api/schedule/dpo-memberships/", {
            "group": self.dpo_group.pk, "student": self.vera.pk}, format="json")
        self.assertEqual(member.status_code, 201, member.content)
        bad_member = self.client.post("/api/schedule/dpo-memberships/", {
            "group": self.g2.pk, "student": self.vera.pk}, format="json")
        self.assertEqual(bad_member.status_code, 400)
