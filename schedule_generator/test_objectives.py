from dataclasses import replace
from datetime import date, time
from types import SimpleNamespace

from django.test import SimpleTestCase
from ortools.sat.python import cp_model

from schedule_generator.enums import Format, LessonType
from schedule_generator.services.config import SolverConfig
from schedule_generator.services.dto import EventSpec, FixedPDSpec
from schedule_generator.services.generator import generate_schedule
from schedule_generator.services.objectives import collect_window_penalties


def spec(**options):
    values = dict(
        event_id=1,
        lesson_type=LessonType.PRACTICE,
        format=Format.ONLINE,
        pairs_per_week=2,
        audience_size=30,
        teacher_id=None,
        blocking_group_ids={1},
    )
    return EventSpec(**(values | options))


def slot(pk, start, end):
    return SimpleNamespace(
        id=pk,
        day_of_week=1,
        lesson_number=pk,
        start_time=time.fromisoformat(start),
        end_time=time.fromisoformat(end),
    )


def fixed_pd():
    return FixedPDSpec(
        session_id=1,
        project_id=1,
        day_of_week=1,
        start_minute=750,
        end_minute=840,
        teacher_id=9,
        student_ids=frozenset({1}),
        participant_group_ids=frozenset({1}),
        room_id=None,
        place_raw="Тестовое место",
        travel_buffer_minutes=30,
        occurrence_dates=frozenset({date(2026, 9, 1)}),
    )


class WindowPenaltyTests(SimpleTestCase):
    def cost(self, event, occupancy, slots=(), sessions=()):
        model = cp_model.CpModel()
        ids = list(occupancy)
        placements = {
            (event.event_id, pk): model.NewBoolVar(f"class_{pk}") for pk in ids
        }
        for pk, busy in occupancy.items():
            model.Add(placements[event.event_id, pk] == busy)
        penalties = collect_window_penalties(
            model,
            [event],
            ids,
            dict.fromkeys(ids, 1),
            placements,
            slots=slots,
            fixed_pd=sessions,
        )
        model.Minimize(sum(penalties))
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        self.assertEqual(solver.Solve(model), cp_model.OPTIMAL)
        return solver.Value(sum(penalties))

    def test_forced_empty_slot_between_two_classes_has_positive_cost(self):
        self.assertGreater(self.cost(spec(), {1: 1, 2: 0, 3: 1}), 0)

    def test_teacher_windows_are_counted_without_group_or_student(self):
        self.assertGreater(
            self.cost(spec(teacher_id=1, blocking_group_ids=set()), {1: 1, 2: 0, 3: 1}),
            0,
        )

    def test_regular_break_and_empty_edges_are_not_windows(self):
        self.assertEqual(
            self.cost(
                spec(),
                {1: 1, 2: 1},
                [slot(1, "09:00", "10:30"), slot(2, "10:45", "12:15")],
            ),
            0,
        )
        self.assertEqual(self.cost(spec(), {1: 0, 2: 1, 3: 0}), 0)

    def test_longer_wait_costs_more_in_real_minutes(self):
        short = self.cost(
            spec(), {1: 1, 2: 1}, [slot(1, "09:00", "10:30"), slot(2, "12:30", "14:00")]
        )
        long = self.cost(
            spec(), {1: 1, 2: 1}, [slot(1, "09:00", "10:30"), slot(2, "15:30", "17:00")]
        )
        self.assertEqual((short, long), (90, 270))

    def test_student_windows_join_group_classes_with_only_own_fixed_pd(self):
        # Only student 1 attends PD; student 2's free afternoon is not a window.
        cost = self.cost(
            spec(attending_student_ids={1, 2}),
            {1: 1, 2: 0},
            [slot(1, "09:00", "10:30"), slot(2, "10:45", "12:15")],
            [fixed_pd()],
        )
        self.assertEqual(cost, 90)

    def test_teacher_windows_include_fixed_pd(self):
        cost = self.cost(
            spec(teacher_id=9, blocking_group_ids=set()),
            {1: 1, 2: 0},
            [slot(1, "09:00", "10:30"), slot(2, "10:45", "12:15")],
            [fixed_pd()],
        )
        self.assertEqual(cost, 90)

    def test_overlapping_fixed_intervals_use_union_for_weekly_window(self):
        shorter = replace(
            fixed_pd(),
            session_id=2,
            start_minute=780,
            end_minute=810,
            occurrence_dates=frozenset({date(2026, 9, 8)}),
        )
        cost = self.cost(
            spec(attending_student_ids={1}),
            {1: 1},
            [slot(1, "15:00", "16:30")],
            [fixed_pd(), shorter],
        )
        # The union ends at 14:00, not at the nested interval's 13:30.
        self.assertEqual(cost, 30)

    def test_generated_pd_and_common_classes_share_personal_occupancy(self):
        model = cp_model.CpModel()
        group = spec(attending_student_ids={1, 2})
        project = spec(
            event_id=2,
            lesson_type=LessonType.PD,
            blocking_group_ids=set(),
            blocking_student_ids={1},
            attending_student_ids={1},
        )
        slots = [
            slot(1, "09:00", "10:30"),
            slot(2, "10:45", "12:15"),
            slot(3, "12:30", "14:00"),
        ]
        placements = {
            (e, s): model.NewBoolVar(f"class_{e}_{s}")
            for e in (1, 2)
            for s in (1, 2, 3)
        }
        for key, variable in placements.items():
            model.Add(variable == int(key in ((1, 1), (2, 3))))
        penalties = collect_window_penalties(
            model,
            [group, project],
            [1, 2, 3],
            {1: 1, 2: 1, 3: 1},
            placements,
            slots=slots,
        )
        model.Minimize(sum(penalties))
        solver = cp_model.CpSolver()
        self.assertEqual(solver.Solve(model), cp_model.OPTIMAL)
        self.assertEqual(solver.Value(sum(penalties)), 90)

    def test_required_pairs_are_not_dropped_to_hide_a_large_unavoidable_window(self):
        result = generate_schedule(
            slots=[slot(1, "09:00", "10:30"), slot(2, "18:00", "19:30")],
            rooms=[],
            specs=[spec(attending_student_ids=set(range(1, 1001)))],
            teachers_by_id={},
            config=SolverConfig(time_limit_seconds=2, seed=17, num_search_workers=1),
        )
        self.assertEqual((result.status, len(result.assignments)), ("SOLVED", 2))

    def test_lecture_priority_breaks_tie_when_only_one_pair_fits(self):
        result = generate_schedule(
            slots=[slot(1, "09:00", "10:30")],
            rooms=[],
            specs=[
                spec(pairs_per_week=1),
                spec(event_id=2, pairs_per_week=1, lesson_type=LessonType.LECTURE),
            ],
            teachers_by_id={},
            config=SolverConfig(time_limit_seconds=2, seed=17, num_search_workers=1),
        )
        self.assertEqual([item["event_id"] for item in result.assignments], [2])
