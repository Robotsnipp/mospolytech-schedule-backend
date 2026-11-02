"""Regression solutions for weekday and subject-block preferences (real CP-SAT)."""

from dataclasses import replace
from datetime import time
from types import SimpleNamespace

from django.test import SimpleTestCase
from ortools.sat.python import cp_model

from schedule_generator.enums import Format, LessonType
from schedule_generator.services.config import SolverConfig
from schedule_generator.services.dto import EventSpec
from schedule_generator.services.generator import generate_schedule
from schedule_generator.services.teaching_preferences import (
    collect_teaching_preferences,
)


def slot(pk, day, number, start="09:00", end="10:30"):
    return SimpleNamespace(id=pk, day_of_week=day, lesson_number=number,
                           start_time=time.fromisoformat(start), end_time=time.fromisoformat(end))


def event(pk, kind=LessonType.PRACTICE, *, subject=1, groups=None, pairs=1, stream=None):
    return EventSpec(
        event_id=pk, lesson_type=kind,
        format=Format.ONLINE if kind == LessonType.LECTURE else Format.ONSITE,
        pairs_per_week=pairs, audience_size=30, teacher_id=None,
        blocking_group_ids={1} if groups is None else groups,
        subject_id=subject, stream_id=stream,
    )


def tuesday(count=4):
    times = [("09:00", "10:30"), ("10:40", "12:10"),
             ("12:20", "13:50"), ("14:30", "16:00")]
    return [slot(i + 1, 1, i + 1, *times[i]) for i in range(count)]


class TeachingPreferenceTests(SimpleTestCase):
    def solve(self, events, slots):
        result = generate_schedule(
            specs=events, slots=slots,
            rooms=[SimpleNamespace(id=1, capacity=30, building_id=None)],
            teachers_by_id={},
            config=SolverConfig(time_limit_seconds=5, seed=17, num_search_workers=1),
        )
        self.assertEqual(result.status, "SOLVED", result.message)
        self.assertIn("status=OPTIMAL", result.message)
        self.assertEqual(len(result.assignments), sum(e.pairs_per_week for e in events))
        return result.assignments

    def pinned_counts(self, events, slots, placements):
        """Solve exact preference counters with fixed input, independent of tie-breaking."""
        model = cp_model.CpModel()
        variables = {(e.event_id, s.id): model.NewBoolVar(f"place_{e.event_id}_{s.id}")
                     for e in events for s in slots}
        for key, variable in variables.items():
            model.Add(variable == int(key in placements))
        counters = collect_teaching_preferences(
            model=model, specs=events, slots=slots, pair_placement_vars=variables,
        )
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        self.assertEqual(solver.Solve(model), cp_model.OPTIMAL)
        return {key: solver.Value(value) for key, value in counters.items()}

    def test_weekdays_win_even_when_saturday_has_a_better_window(self):
        slots = [slot(1, 1, 1), slot(2, 1, 2, "18:00", "19:30"),
                 slot(3, 5, 1), slot(4, 5, 2, "10:40", "12:10")]
        result = self.solve([event(1, subject=None, pairs=2)], slots)
        days = {s.id: s.day_of_week for s in slots}
        self.assertEqual([days[x["slot_id"]] for x in result], [1, 1])

    def test_unavoidable_saturday_does_not_cost_a_required_pair(self):
        slots = [slot(1, 1, 1), slot(2, 5, 1)]
        result = self.solve([event(1, pairs=2)], slots)
        self.assertEqual({x["slot_id"] for x in result}, {1, 2})

    def test_one_saturday_pair_can_avoid_a_multi_hour_gap_for_thirty_students(self):
        slots = [slot(1, 1, 1), slot(2, 1, 2, "18:00", "19:30"),
                 slot(3, 5, 1), slot(4, 5, 2, "10:40", "12:10")]
        course = replace(event(1, subject=None, pairs=2), attending_student_ids=set(range(1, 31)))
        result = self.solve([course], slots)
        days = {item.id: item.day_of_week for item in slots}
        self.assertEqual(sum(days[item['slot_id']] == 5 for item in result), 1)

    def assert_lecture_before_applied(self, applied_kind, lecture_groups=None, applied_groups=None):
        slots = tuesday(3)
        lecture = event(1, LessonType.LECTURE, groups=lecture_groups, stream=10)
        applied = event(2, applied_kind, groups=applied_groups)
        unrelated = event(3, subject=2, groups=applied_groups)
        result = self.solve([lecture, applied, unrelated], slots)
        positions = {x["event_id"]: x["slot_id"] for x in result}
        self.assertEqual(positions[2], positions[1] + 1)
        self.assertFalse(positions[1] < positions[3] < positions[2])
        self.assertEqual(applied.lesson_type, applied_kind)

    def test_lecture_immediately_precedes_practice_instead_of_interleaving(self):
        self.assert_lecture_before_applied(LessonType.PRACTICE)

    def test_lecture_immediately_precedes_lab_instead_of_interleaving(self):
        self.assert_lecture_before_applied(LessonType.LAB)

    def test_lecture_stream_and_its_group_receive_the_same_preference(self):
        self.assert_lecture_before_applied(LessonType.PRACTICE, {1, 2}, {2})

    def test_repeated_practice_and_lab_form_one_subject_block(self):
        slots = tuesday(4)
        events = [event(1, pairs=2), event(2, LessonType.LAB), event(3, subject=2)]
        result = self.solve(events, slots)
        positions = sorted(x["slot_id"] for x in result if x["event_id"] in {1, 2})
        self.assertEqual(positions[-1] - positions[0], 2)
        self.assertEqual(sum(x["event_id"] == 1 for x in result), 2)
        self.assertEqual(sum(x["event_id"] == 2 for x in result), 1)
        self.assertEqual(events[0].lesson_type, LessonType.PRACTICE)
        self.assertEqual(events[1].lesson_type, LessonType.LAB)

    def test_unrelated_subjects_groups_or_unknown_subjects_receive_no_theme_reward(self):
        variants = [
            (event(1, LessonType.LECTURE), event(2, subject=2)),
            (event(1, LessonType.LECTURE), event(2, groups={2})),
            (event(1, LessonType.LECTURE, stream=9), event(2, groups={2}, stream=9)),
            (event(1, LessonType.LECTURE, subject=None), event(2, subject=None)),
        ]
        for lecture, applied in variants:
            with self.subTest(subject=applied.subject_id, groups=applied.blocking_group_ids,
                              stream=applied.stream_id):
                counts = self.pinned_counts([lecture, applied], tuesday(2), {(1, 1), (2, 2)})
                self.assertEqual(counts["lecture_applied"], 0)
                self.assertEqual(counts["subject_blocks"], 0)

    def test_skipped_pair_cross_day_and_overlapping_times_are_not_adjacency(self):
        variants = [
            [slot(1, 1, 1), slot(2, 1, 3, "12:20", "13:50")],
            [slot(1, 1, 1), slot(2, 3, 2, "10:40", "12:10")],
            [slot(1, 1, 1), slot(2, 1, 2, "10:00", "11:30")],
        ]
        for slots in variants:
            with self.subTest(slots=slots):
                counts = self.pinned_counts([event(1, LessonType.LECTURE), event(2)],
                                            slots, {(1, 1), (2, 2)})
                self.assertEqual(counts["lecture_applied"], 0)
                self.assertEqual(counts["subject_blocks"], 0)

    def test_applied_before_lecture_is_a_block_but_not_the_preferred_direction(self):
        counts = self.pinned_counts([event(1, LessonType.LECTURE), event(2)],
                                    tuesday(2), {(2, 1), (1, 2)})
        self.assertEqual(counts["lecture_applied"], 0)
        self.assertEqual(counts["subject_blocks"], 1)

    def test_practice_and_lab_have_equal_block_scores_without_merging_types(self):
        events = [event(1), event(2, LessonType.LAB)]
        forward = self.pinned_counts(events, tuesday(2), {(1, 1), (2, 2)})
        reverse = self.pinned_counts(events, tuesday(2), {(2, 1), (1, 2)})
        self.assertEqual(forward, reverse)
        self.assertEqual(forward["subject_blocks"], 1)
        self.assertEqual(forward["lecture_applied"], 0)
        self.assertEqual([e.lesson_type for e in events], [LessonType.PRACTICE, LessonType.LAB])
