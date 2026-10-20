"""Правила заботы о людях на реальном решателе CP-SAT (фичи 1–4).

Каждый тест строит маленький сценарий, запускает ``run_generation`` и смотрит
на фактическое расписание: статус SOLVED сам по себе ничего не доказывает.
"""

from datetime import date

from django.test import SimpleTestCase, TestCase
from ortools.sat.python import cp_model
from rest_framework.test import APIClient

from schedule_generator.enums import Chronotype, Format, LessonType
from schedule_generator.models import (
    Building,
    Group,
    LessonEvent,
    Location,
    Room,
    ScheduledClass,
    Semester,
    Subject,
    Teacher,
    TimeSlot,
    WeekPattern,
)
from schedule_generator.services import wellbeing
from schedule_generator.services.config import FATIGUE_COMFORT_LEVEL
from schedule_generator.services.constraints import add_location_consistency
from schedule_generator.services.dto import EventSpec
from schedule_generator.services.generation import run_generation
from schedule_generator.services.objectives import collect_fatigue_penalties

TUESDAY, THURSDAY = 1, 3   # обычные пары идут вт, чт, пт, сб (day_rules.py)
SEEDS = range(1, 7)


class WellbeingTestCase(TestCase):
    def setUp(self):
        self.semester = Semester.objects.create(name="W", start_date=date(2026, 9, 1))
        self.pattern = WeekPattern.objects.create(semester=self.semester)
        self.subject = Subject.objects.create(name="Math")
        self.group = Group.objects.create(name="261-101")
        self.room = Room.objects.create(name="R1", capacity=30)

    def slot(self, day, lesson_number):
        return TimeSlot.objects.create(
            week_pattern=self.pattern, day_of_week=day, lesson_number=lesson_number)

    def event(self, group=None, teacher=None, pairs=1):
        return LessonEvent.objects.create(
            semester=self.semester, subject=self.subject, group=group or self.group,
            lesson_type=LessonType.PRACTICE, teacher=teacher, pairs_per_week=pairs)

    def two_day_scenario(self):
        """Два дня по два слота и два события одной группы: без режима сессии
        нагрузка расходится по дням, с ним собирается в один день."""
        for day in (TUESDAY, THURSDAY):
            self.slot(day, 2)
            self.slot(day, 3)
        self.event()
        self.event()

    def generate(self, **kwargs):
        outcome = run_generation(semester=self.semester, time_limit_seconds=5, **kwargs)
        self.assertEqual(outcome.status, "SOLVED")
        # список, а не queryset: следующий запуск с clear_previous удалит эти пары
        return list(ScheduledClass.objects.filter(run_id=outcome.run_id).select_related("slot"))

    def generate_for_every_seed(self, **kwargs):
        """Результаты для нескольких seed: при равных вариантах решатель выбирает
        произвольно, поэтому предпочтение доказано, только если оно держится на всех."""
        return [self.generate(seed=seed, **kwargs) for seed in SEEDS]


class WellbeingFormulaTests(SimpleTestCase):
    def test_day_fatigue_counts_pairs_early_start_and_late_pairs(self):
        self.assertEqual(wellbeing.day_fatigue([]), 0)
        self.assertEqual(wellbeing.day_fatigue([3]), 10)
        self.assertEqual(wellbeing.day_fatigue([1, 2]), 25)      # 2 пары + ранний старт
        self.assertEqual(wellbeing.day_fatigue([6, 7]), 30)      # 2 пары + 2 поздние

    def test_windows_are_free_slots_between_first_and_last_pair(self):
        self.assertEqual(wellbeing.count_windows([2]), 0)
        self.assertEqual(wellbeing.count_windows([2, 3, 4]), 0)
        self.assertEqual(wellbeing.count_windows([2, 5]), 2)


class FatigueModelConsistencyTests(SimpleTestCase):
    """Усталость, которую минимизирует решатель, совпадает с ``wellbeing.day_fatigue``:
    на фиксированных наборах пар значения переменных должны быть равны формуле."""

    def solve_fixed_day(self, busy_lesson_numbers):
        model = cp_model.CpModel()
        lesson_numbers = range(1, 8)
        slot_ids = list(lesson_numbers)
        spec = EventSpec(event_id=1, lesson_type=LessonType.PRACTICE, format=Format.ONLINE,
                         pairs_per_week=7, audience_size=10, teacher_id=None,
                         blocking_group_ids={1})
        placement = {(1, slot_id): model.NewBoolVar(f"p{slot_id}") for slot_id in slot_ids}
        for slot_id in slot_ids:
            model.Add(placement[(1, slot_id)] == int(slot_id in busy_lesson_numbers))
        excess_vars, peak_vars = collect_fatigue_penalties(
            model, [spec], slot_ids, {slot_id: TUESDAY for slot_id in slot_ids},
            {slot_id: slot_id for slot_id in slot_ids}, placement)
        # пик минимизируем первым (вес 1000), чтобы он стал равен усталости дня
        model.Minimize(1000 * sum(peak_vars) + sum(excess_vars))
        solver = cp_model.CpSolver()
        self.assertEqual(solver.Solve(model), cp_model.OPTIMAL)
        peak = sum(solver.Value(var) for var in peak_vars)
        excess = sum(solver.Value(var) for var in excess_vars)
        return peak, excess

    def test_solver_fatigue_matches_formula(self):
        for busy in ([], [1], [2, 3], [1, 2, 3, 4, 5], [5, 6, 7], [2, 3, 4, 5, 6]):
            with self.subTest(busy=busy):
                peak, excess = self.solve_fixed_day(set(busy))
                fatigue = wellbeing.day_fatigue(busy)
                self.assertEqual(peak, fatigue)
                self.assertEqual(excess, max(0, fatigue - FATIGUE_COMFORT_LEVEL))
                self.assertEqual(wellbeing.is_overloaded(fatigue), excess > 0)


class FatigueTests(WellbeingTestCase):
    def test_solver_avoids_early_slot_when_alternative_exists(self):
        self.slot(TUESDAY, 1)
        self.slot(TUESDAY, 2)
        self.event()
        for placed in self.generate_for_every_seed():
            self.assertEqual([c.slot.lesson_number for c in placed], [2])

    def test_solver_evens_out_load_across_days(self):
        for day in (TUESDAY, THURSDAY):
            for lesson_number in (2, 3, 4):
                self.slot(day, lesson_number)
        for _ in range(4):
            self.event()
        placed = self.generate()
        per_day = {}
        for scheduled in placed:
            per_day[scheduled.slot.day_of_week] = per_day.get(scheduled.slot.day_of_week, 0) + 1
        self.assertEqual(sorted(per_day.values()), [2, 2])


class ChronotypeTests(WellbeingTestCase):
    def test_owl_teacher_gets_later_pair(self):
        self.slot(TUESDAY, 2)
        self.slot(TUESDAY, 3)
        teacher = Teacher.objects.create(full_name="Сова", chronotype=Chronotype.LATE)
        self.event(teacher=teacher)
        for placed in self.generate_for_every_seed():
            self.assertEqual([c.slot.lesson_number for c in placed], [3])

    def test_lark_teacher_gets_earlier_pair(self):
        self.slot(TUESDAY, 4)
        self.slot(TUESDAY, 5)
        teacher = Teacher.objects.create(full_name="Жаворонок", chronotype=Chronotype.EARLY)
        self.event(teacher=teacher)
        for placed in self.generate_for_every_seed():
            self.assertEqual([c.slot.lesson_number for c in placed], [4])

    def test_chronotype_never_blocks_placement(self):
        self.slot(TUESDAY, 2)
        teacher = Teacher.objects.create(full_name="Сова", chronotype=Chronotype.LATE)
        self.event(teacher=teacher)
        self.assertEqual(len(self.generate()), 1)


class FreeDaysTests(WellbeingTestCase):
    def setUp(self):
        super().setUp()
        self.two_day_scenario()

    def days_used(self, placed):
        return {c.slot.day_of_week for c in placed}

    def test_without_mode_load_is_spread_over_days(self):
        for placed in self.generate_for_every_seed():
            self.assertEqual(len(self.days_used(placed)), 2)

    def test_session_mode_frees_days(self):
        for placed in self.generate_for_every_seed(free_days_per_group=3):
            self.assertEqual(len(self.days_used(placed)), 1)


class SessionModeApiTests(WellbeingTestCase):
    def setUp(self):
        super().setUp()
        self.two_day_scenario()

    def post(self, **extra):
        return APIClient().post(
            "/api/schedule/generate/",
            {"semester": self.semester.pk, "time_limit_seconds": 5, **extra}, format="json")

    def test_endpoint_applies_free_days(self):
        response = self.post(free_days_per_group=3)
        self.assertEqual(response.status_code, 201, response.content)
        placed = ScheduledClass.objects.filter(run_id=response.json()["id"])
        self.assertEqual(len({c.slot.day_of_week for c in placed.select_related("slot")}), 1)

    def test_endpoint_rejects_out_of_range_value(self):
        self.assertEqual(self.post(free_days_per_group=4).status_code, 400)
        self.assertEqual(self.post(free_days_per_group=-1).status_code, 400)

    def test_default_keeps_the_mode_off(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.content)
        placed = ScheduledClass.objects.filter(run_id=response.json()["id"])
        self.assertEqual(len({c.slot.day_of_week for c in placed.select_related("slot")}), 2)


class TravelRuleModelTests(SimpleTestCase):
    """Ограничение дороги на маленькой модели CP-SAT с зафиксированными аудиториями:
    событие 1 возможно только в аудитории на локации 100, событие 2 — на локации 200."""

    def solve(self, lesson_numbers, travel_buffer_slots):
        model = cp_model.CpModel()
        slot_ids = list(range(len(lesson_numbers)))
        specs = [
            EventSpec(event_id=event_id, lesson_type=LessonType.PRACTICE,
                      format=Format.ONSITE, pairs_per_week=1, audience_size=10,
                      teacher_id=None, blocking_group_ids={1})
            for event_id in (1, 2)
        ]
        placement = {(spec.event_id, slot_id): model.NewBoolVar(f"p{spec.event_id}_{slot_id}")
                     for spec in specs for slot_id in slot_ids}
        room_by_event = {1: 10, 2: 20}
        room_choice = {}
        for spec in specs:
            model.Add(sum(placement[(spec.event_id, slot_id)] for slot_id in slot_ids) == 1)
            for slot_id in slot_ids:
                choice = model.NewBoolVar(f"r{spec.event_id}_{slot_id}")
                model.Add(choice == placement[(spec.event_id, slot_id)])
                room_choice[(spec.event_id, slot_id, room_by_event[spec.event_id])] = choice
        add_location_consistency(
            model, specs, slot_ids, placement, room_choice, {10: 100, 20: 200},
            {slot_id: TUESDAY for slot_id in slot_ids}, dict(enumerate(lesson_numbers)),
            travel_buffer_slots)
        return cp_model.CpSolver().Solve(model)

    def test_adjacent_slots_on_different_locations_are_infeasible(self):
        self.assertEqual(self.solve([2, 3], travel_buffer_slots=1), cp_model.INFEASIBLE)

    def test_one_free_slot_between_locations_is_enough(self):
        self.assertIn(self.solve([2, 4], travel_buffer_slots=1),
                      (cp_model.OPTIMAL, cp_model.FEASIBLE))

    def test_buffer_zero_disables_the_rule(self):
        self.assertIn(self.solve([2, 3], travel_buffer_slots=0),
                      (cp_model.OPTIMAL, cp_model.FEASIBLE))

    def test_same_slot_on_different_locations_stays_forbidden(self):
        self.assertEqual(self.solve([2], travel_buffer_slots=0), cp_model.INFEASIBLE)


class TravelBufferEndToEndTests(WellbeingTestCase):
    def test_adjacent_pairs_on_one_location_are_still_allowed(self):
        self.room.delete()
        location = Location.objects.create(name="Одна")
        Room.objects.create(name="A", capacity=30,
                            building=Building.objects.create(location=location, name="1"))
        self.slot(TUESDAY, 2)
        self.slot(TUESDAY, 3)
        self.event()
        self.event()
        placed = self.generate()
        self.assertEqual(sorted(c.slot.lesson_number for c in placed), [2, 3])
