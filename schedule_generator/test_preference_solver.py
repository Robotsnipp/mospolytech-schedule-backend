from types import SimpleNamespace

from django.test import SimpleTestCase
from ortools.sat.python import cp_model

from schedule_generator.enums import LessonType
from schedule_generator.services.config import PlanningPreferences, SolverConfig
from schedule_generator.services.generator import _build_model, generate_schedule
from schedule_generator.services.solution import extract_result
from schedule_generator.test_teaching_preferences import event, slot


class PreferenceSolverTests(SimpleTestCase):
    def test_three_groups_have_valid_int64_model_and_real_optimal_solution(self):
        slots = [slot(day * 10 + n, day, n, start, end)
                 for day in (1, 3, 5)
                 for n, start, end in [(1, "09:00", "10:30"), (2, "10:40", "12:10"),
                                        (3, "12:20", "13:50")]]
        events = []
        for group in (1, 2, 3):
            for subject in (1, 2):
                for kind in (LessonType.LECTURE, LessonType.PRACTICE, LessonType.LAB):
                    events.append(event(len(events) + 1, kind, subject=subject, groups={group}))
        rooms = [SimpleNamespace(id=n, capacity=30, building_id=None) for n in range(1, 4)]
        model, _ = _build_model(slots=slots, rooms=rooms, specs=events, teachers_by_id={},
            fixed_pd=(), preferences=PlanningPreferences(), pinned=[], forbidden=set())
        self.assertEqual(model.Validate(), "")
        result = generate_schedule(slots=slots, rooms=rooms, specs=events, teachers_by_id={},
            config=SolverConfig(time_limit_seconds=5, seed=17, num_search_workers=1))
        self.assertEqual(result.status, "SOLVED", result.message)
        self.assertEqual(len(result.assignments), 18)
        self.assertIn("saturday=0", result.message)
        self.assertIn("lecture_applied=6", result.message)
        self.assertIn("subject_blocks=12", result.message)
        self.assertEqual(result.solver_diagnostics["status"], "OPTIMAL")
        self.assertTrue(result.solver_diagnostics["proven_optimal"])
        self.assertAlmostEqual(result.solver_diagnostics["incumbent_objective"],
                               result.solver_diagnostics["best_objective_bound"])
        by_id = {item.event_id: item for item in events}
        slot_by_id = {item.id: item for item in slots}
        for group in (1, 2, 3):
            for subject in (1, 2):
                block = sorted((slot_by_id[item['slot_id']].day_of_week,
                                slot_by_id[item['slot_id']].lesson_number,
                                by_id[item['event_id']].lesson_type)
                               for item in result.assignments
                               if by_id[item['event_id']].subject_id == subject
                               and group in by_id[item['event_id']].blocking_group_ids)
                self.assertEqual(block[0][2], LessonType.LECTURE)
                self.assertEqual(block[-1][1] - block[0][1], 2)
                self.assertEqual(block[-1][0], block[0][0])

    def test_real_zero_budget_unknown_is_not_misreported_as_infeasible(self):
        model = cp_model.CpModel()
        value = model.NewBoolVar("placement")
        model.Maximize(value)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 0
        status = solver.Solve(model)
        self.assertEqual(status, cp_model.UNKNOWN)
        result = extract_result(solver=solver, status_name=solver.StatusName(status), feasible=False,
            specs=[event(1)], room_ids=[], pair_placement_vars={}, onsite_room_choice_vars={})
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(result.assignments, [])
        self.assertIsNone(result.objective_value)

    def test_invalid_model_and_proven_infeasibility_are_distinct(self):
        for solver_status, expected in (("MODEL_INVALID", "ERROR"), ("INFEASIBLE", "INFEASIBLE")):
            with self.subTest(status=solver_status):
                result = extract_result(solver=None, status_name=solver_status, feasible=False,
                    specs=[event(1)], room_ids=[], pair_placement_vars={}, onsite_room_choice_vars={})
                self.assertEqual(result.status, expected)
