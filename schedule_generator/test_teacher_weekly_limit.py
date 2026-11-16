"""Недельный лимит пар преподавателя: проверка результата на реальном CP-SAT."""

from schedule_generator.models import GenerationIssue, ScheduledClass
from schedule_generator.services.teacher_limits import weekly_teacher_load
from schedule_generator.test_dpo_programs import DPOScenario


class WeeklyLimitTests(DPOScenario):
    def warnings(self, run_id):
        return list(GenerationIssue.objects.filter(run_id=run_id, text__contains="пар в неделю")
                    .values_list("text", flat=True))

    def test_over_limit_warns_but_places_all_pairs(self):
        self.main_teacher.max_pairs_per_week = 2
        self.main_teacher.save()
        self.main_lesson(self.g1, pairs=2)
        self.main_lesson(self.g2, pairs=1)
        run = self.main_run()  # SOLVED: лимит не мешает полноте
        self.assertEqual(run.scheduled_classes.count(), 3)
        self.assertEqual(self.warnings(run.pk),
                         ["Преподаватель Main teacher: 3 пар в неделю при лимите 2. "
                          "Перераспределите нагрузку или измените лимит."])

    def test_within_limit_no_warning(self):
        self.main_teacher.max_pairs_per_week = 3
        self.main_teacher.save()
        self.main_lesson(self.g1, pairs=2)
        self.main_lesson(self.g2, pairs=1)
        self.assertEqual(self.warnings(self.main_run().pk), [])

    def test_only_this_teachers_pairs_count(self):
        self.main_teacher.max_pairs_per_week = 1
        self.main_teacher.save()
        self.main_lesson(self.g1, pairs=1)
        self.main_lesson(self.g2, pairs=2, teacher=self.dpo_teacher)
        self.assertEqual(self.warnings(self.main_run().pk), [])

    def test_dpo_run_counts_main_schedule_of_the_teacher(self):
        # В основном расписании у преподавателя ДПО уже 1 пара, в программе ещё 1.
        self.dpo_teacher.max_pairs_per_week = 1
        self.dpo_teacher.save()
        self.main_lesson(self.g2, teacher=self.dpo_teacher)
        base = self.main_run()
        self.assertEqual(self.warnings(base.pk), [])
        self.discipline(format="ONLINE")
        result = self.plan()
        self.assertEqual(result["status"], "SOLVED")
        self.assertEqual(ScheduledClass.objects.filter(run_id=result["run_id"]).count(), 1)
        self.assertEqual(len(self.warnings(result["run_id"])), 1)
        self.assertIn("DPO teacher: 2 пар", self.warnings(result["run_id"])[0])


class WeeklyLoadUnitTests(DPOScenario):
    def test_load_counts_assignments_and_fixed_busy(self):
        from schedule_generator.services.dto import EventSpec, FixedPDSpec
        specs = [EventSpec(event_id=1, lesson_type="PRACTICE", format="ONSITE", pairs_per_week=2,
                           audience_size=1, teacher_id=7),
                 EventSpec(event_id=2, lesson_type="PRACTICE", format="ONSITE", pairs_per_week=1,
                           audience_size=1, teacher_id=None)]
        busy = [FixedPDSpec(session_id=1, project_id=1, day_of_week=0, start_minute=0, end_minute=90,
                            teacher_id=7, student_ids=frozenset(), participant_group_ids=frozenset(),
                            room_id=None, place_raw="", travel_buffer_minutes=0,
                            occurrence_dates=frozenset()),
                FixedPDSpec(session_id=2, project_id=0, day_of_week=0, start_minute=0, end_minute=90,
                            teacher_id=None, student_ids=frozenset(), participant_group_ids=frozenset(),
                            room_id=None, place_raw="", travel_buffer_minutes=0,
                            occurrence_dates=frozenset())]
        assignments = [{"event_id": 1}, {"event_id": 1}, {"event_id": 2}]
        self.assertEqual(weekly_teacher_load(assignments, specs, busy), {7: 3})
