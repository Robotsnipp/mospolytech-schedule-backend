"""Личное расписание: основной запуск + пары программ ДПО (реальный CP-SAT)."""

from datetime import date

from rest_framework.test import APIClient

from schedule_generator.models import DPOProgram, Module
from schedule_generator.services.dpo import plan_program
from schedule_generator.test_dpo_programs import DPOScenario


class MergedScheduleTests(DPOScenario):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.main_lesson(self.g1)
        self.base = self.main_run()
        self.discipline()
        self.dpo = self.plan()

    def read(self, kind, pk, **params):
        query = {"semester": self.semester.pk, "module": self.module.pk, **params}
        response = self.client.get(f"/api/schedule/schedule/{kind}/{pk}/", query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def runs_of(self, data):
        return sorted(item["run"] for item in data["scheduled_classes"])

    def test_member_sees_main_and_dpo_pairs(self):
        data = self.read("students", self.anna.pk)
        self.assertEqual(data["run"]["id"], self.base.pk)
        self.assertEqual(data["dpo_runs"], [self.dpo["run_id"]])
        self.assertEqual(self.runs_of(data), [self.base.pk, self.dpo["run_id"]])
        self.assertIn("DPO", {item["lesson_type"] for item in data["scheduled_classes"]})

    def test_non_member_sees_only_main(self):
        data = self.read("students", self.vera.pk)
        self.assertEqual(data["dpo_runs"], [])
        self.assertEqual(self.runs_of(data), [self.base.pk])

    def test_teacher_sees_dpo_pairs(self):
        data = self.read("teachers", self.dpo_teacher.pk)
        self.assertEqual(data["dpo_runs"], [self.dpo["run_id"]])
        self.assertEqual(self.runs_of(data), [self.dpo["run_id"]])
        main = self.read("teachers", self.main_teacher.pk)
        self.assertEqual((main["dpo_runs"], self.runs_of(main)), ([], [self.base.pk]))

    def test_explicit_run_id_is_not_merged(self):
        for params in ({"run_id": self.base.pk},
                       {"run_id": self.base.pk, "semester": self.semester.pk, "module": self.module.pk}):
            data = self.client.get(f"/api/schedule/schedule/students/{self.anna.pk}/", params).json()
            self.assertEqual((data["dpo_runs"], self.runs_of(data)), ([], [self.base.pk]))

    def test_main_group_gets_nothing_extra_dpo_group_gets_its_pairs(self):
        data = self.read("groups", self.g1.pk)
        self.assertEqual((data["dpo_runs"], self.runs_of(data)), ([], [self.base.pk]))
        dpo_group = self.read("groups", self.dpo_group.pk)
        self.assertEqual((dpo_group["dpo_runs"], self.runs_of(dpo_group)),
                         ([self.dpo["run_id"]], [self.dpo["run_id"]]))

    def test_only_latest_run_of_program(self):
        again = plan_program(self.program, time_limit_seconds=5)
        data = self.read("students", self.anna.pk)
        self.assertEqual(data["dpo_runs"], [again["run_id"]])
        self.assertEqual(self.runs_of(data), [self.base.pk, again["run_id"]])

    def test_dpo_of_other_period_is_not_merged(self):
        october = Module.objects.create(semester=self.semester, number=2, start_date=date(2026, 10, 1),
                                        end_date=date(2026, 10, 31), teaching_weeks=4)
        other = DPOProgram.objects.create(name="Autumn", semester=self.semester, module=october)
        other.groups.add(self.dpo_group)
        self.discipline(program=other, subject=self.math)
        self.assertEqual(plan_program(other, time_limit_seconds=5)["status"], "SOLVED")
        data = self.read("students", self.anna.pk)
        self.assertEqual(data["dpo_runs"], [self.dpo["run_id"]])
