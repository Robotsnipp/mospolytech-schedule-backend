"""Real-solver regressions for available weekdays and personal fixed-PD occupancy."""

from collections import Counter
from datetime import date, time
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase
from ortools.sat.python import cp_model

from schedule_generator.enums import Format, LessonType
from schedule_generator.models import (
    FixedProjectSession,
    GenerationRun,
    Group,
    LectureStream,
    LessonEvent,
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
from schedule_generator.services.config import SolverConfig
from schedule_generator.services.distribution import (
    collect_daily_distribution_penalties,
)
from schedule_generator.services.dto import EventSpec, FixedPDSpec
from schedule_generator.services.event_specs import build_event_specs
from schedule_generator.services.generation import run_generation
from schedule_generator.services.generator import generate_schedule


class OpenWeekdayAndPDTests(TestCase):
    def setUp(self):
        self.semester = Semester.objects.create(name="Распределение", start_date=date(2026, 9, 1))
        self.pattern = WeekPattern.objects.create(semester=self.semester)
        self.group = Group.objects.create(name="261-101", semester=self.semester)
        self.student = Student.objects.create(group=self.group, full_name="Участник проекта")
        self.peer = Student.objects.create(group=self.group, full_name="Другой участник")
        self.teacher = Teacher.objects.create(full_name="Преподаватель предмета")
        self.room = Room.objects.create(name="Учебная", capacity=30)
        self.subject = Subject.objects.create(name="Дисциплина")

    def slot(self, day, number, start, end):
        return TimeSlot.objects.create(
            week_pattern=self.pattern, day_of_week=day, lesson_number=number,
            start_time=time.fromisoformat(start), end_time=time.fromisoformat(end),
        )

    def lesson(self, **overrides):
        values = dict(semester=self.semester, subject=self.subject, group=self.group,
                      lesson_type=LessonType.PRACTICE, teacher=self.teacher,
                      pairs_per_week=1, audience_size=2)
        values.update(overrides)
        return LessonEvent.objects.create(**values)

    def fixed(self, students, *, day=0, start="10:40", end="12:10", buffer=0):
        supervisor = Teacher.objects.create(full_name=f"Руководитель {Teacher.objects.count()}")
        project = Project.objects.create(
            semester=self.semester, title=f"Проект {supervisor.pk}", supervisor=supervisor,
        )
        for student in students:
            ProjectEnrollment.objects.create(project=project, student=student)
        return FixedProjectSession.objects.create(
            project=project, teacher=supervisor, day_of_week=day,
            start_time=time.fromisoformat(start), end_time=time.fromisoformat(end),
            travel_buffer_minutes=buffer, source_key="test:distribution",
            place_details={"raw": f"Отдельное место {project.pk}", "venue": {"kind": "additional"}},
        )

    def generate(self):
        result = run_generation(semester=self.semester, week_pattern=self.pattern,
                                clear_previous=False, time_limit_seconds=5)
        self.assertEqual(result.status, "SOLVED")
        return GenerationRun.objects.get(pk=result.run_id)

    def assert_real_weekday_is_available(self, day):
        expected = self.slot(day, 1, "09:00", "10:30")
        event = self.lesson()
        specs, skipped = build_event_specs(LessonEvent.objects.filter(pk=event.pk))
        self.assertEqual(skipped, [])
        # Actual production rule conversion, with no patch of allowed_days_for.
        self.assertIn(day, specs[0].allowed_days)
        run = self.generate()
        placed = run.scheduled_classes.get()
        self.assertEqual((placed.event_id, placed.slot_id), (event.pk, expected.pk))

    def test_ordinary_class_can_really_use_monday_without_day_rule_patch(self):
        self.assert_real_weekday_is_available(0)

    def test_ordinary_class_can_really_use_wednesday_without_day_rule_patch(self):
        self.assert_real_weekday_is_available(2)

    def test_one_pd_participant_blocks_common_class_only_within_interval_and_buffer(self):
        self.slot(0, 1, "09:00", "10:30")
        self.slot(0, 2, "10:40", "12:10")
        self.slot(0, 3, "12:20", "13:50")
        after = self.slot(0, 4, "14:30", "16:00")
        self.fixed([self.student], buffer=30)
        ordinary = self.lesson()
        run = self.generate()
        self.assertEqual((run.scheduled_classes.get().event_id, run.scheduled_classes.get().slot_id),
                         (ordinary.pk, after.pk))
        self.assertEqual(run.fixed_pd_snapshot[0]["student_ids"], [self.student.pk])
        self.assertNotIn(self.peer.pk, run.fixed_pd_snapshot[0]["student_ids"])

    def test_independent_projects_of_same_group_can_overlap_and_leave_day_available(self):
        self.slot(0, 1, "09:00", "10:30")
        after = self.slot(0, 2, "10:40", "12:10")
        self.fixed([self.student], start="09:00", end="10:30")
        self.fixed([self.peer], start="09:00", end="10:30")
        ordinary = self.lesson()
        run = self.generate()
        placed = run.scheduled_classes.get()
        self.assertEqual((placed.event_id, placed.slot_id), (ordinary.pk, after.pk))
        self.assertEqual(len(run.fixed_pd_snapshot), 2)
        self.assertEqual({tuple(item["student_ids"]) for item in run.fixed_pd_snapshot},
                         {(self.student.pk,), (self.peer.pk,)})

    def test_shared_stream_is_blocked_by_its_participant_but_unrelated_group_class_is_not(self):
        monday = self.slot(0, 1, "09:00", "10:30")
        wednesday = self.slot(2, 1, "09:00", "10:30")
        other_group = Group.objects.create(name="261-102", semester=self.semester)
        participant = Student.objects.create(group=other_group, full_name="Участник общего потока")
        self.fixed([participant], start="09:00", end="10:30")
        stream = LectureStream.objects.create(semester=self.semester, subject=self.subject)
        stream.groups.set([self.group, other_group])
        lecture = self.lesson(group=None, stream=stream, lesson_type=LessonType.LECTURE)
        practical = self.lesson(subject=Subject.objects.create(name="Другой предмет"))
        run = self.generate()
        placements = {item.event_id: item.slot_id for item in run.scheduled_classes.all()}
        self.assertEqual(placements, {lecture.pk: wednesday.pk, practical.pk: monday.pk})
        specs, _ = build_event_specs(LessonEvent.objects.filter(pk=lecture.pk))
        self.assertEqual(specs[0].blocking_group_ids, {self.group.pk, other_group.pk})
        self.assertEqual(specs[0].attending_student_ids, {self.student.pk, self.peer.pk, participant.pk})

    def test_online_lecture_needs_no_travel_buffer_outside_literal_pd_interval(self):
        before = self.slot(0, 1, "09:00", "10:30")
        self.slot(0, 2, "10:40", "12:10")
        after = self.slot(0, 3, "12:20", "13:50")
        self.fixed([self.student], buffer=30)
        stream = LectureStream.objects.create(semester=self.semester, subject=self.subject)
        stream.groups.add(self.group)
        lecture = self.lesson(group=None, stream=stream, lesson_type=LessonType.LECTURE)
        run = self.generate()
        placement = ScheduledClass.objects.get(run=run, event=lecture)
        self.assertIn(placement.slot_id, {before.pk, after.pk})
        self.assertEqual(placement.slot.day_of_week, 0)


# Distribution is checked independently of other objective terms using exact
# pinned occupancies, and then through real whole-model optimization below.


def ordinary(pk=1, *, kind=LessonType.PRACTICE, subject=1, pairs=1, students=None, groups=None):
    return EventSpec(
        event_id=pk, subject_id=subject, lesson_type=kind, format=Format.ONLINE,
        pairs_per_week=pairs, audience_size=4, teacher_id=None,
        blocking_group_ids={1} if groups is None else groups,
        attending_student_ids={1, 2, 3, 4} if students is None else students,
        allowed_days=frozenset(range(6)),
    )


def timed_slot(pk, day, number, start="12:20", end="13:50"):
    return SimpleNamespace(id=pk, day_of_week=day, lesson_number=number,
                           start_time=time.fromisoformat(start), end_time=time.fromisoformat(end))


def fixed_spec(pk, day, start, end, students, *, teacher=90, dates=None):
    return FixedPDSpec(
        session_id=pk, project_id=pk, day_of_week=day,
        start_minute=start, end_minute=end, teacher_id=teacher,
        student_ids=frozenset(students), participant_group_ids=frozenset({1}),
        room_id=None, place_raw=f"Площадка {pk}", travel_buffer_minutes=0,
        occurrence_dates=frozenset(dates or {date(2026, 9, 7 + day)}),
    )


class DailyDistributionTests(SimpleTestCase):
    def cost(self, specs, slots, occupancy, fixed=()):
        model = cp_model.CpModel()
        placements = {(event.event_id, slot.id): model.NewBoolVar(f"p_{event.event_id}_{slot.id}")
                      for event in specs for slot in slots}
        for key, variable in placements.items():
            model.Add(variable == int(key in occupancy))
        costs = collect_daily_distribution_penalties(
            model=model, specs=specs, slots=slots,
            pair_placement_vars=placements, fixed_pd=fixed,
        )
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        self.assertEqual(solver.Solve(model), cp_model.OPTIMAL)
        return solver.Value(sum(costs))

    def solve(self, specs, slots):
        result = generate_schedule(
            specs=specs, slots=slots, rooms=[], teachers_by_id={},
            config=SolverConfig(time_limit_seconds=5, seed=17, num_search_workers=1),
        )
        self.assertEqual(result.status, "SOLVED", result.message)
        self.assertIn("status=OPTIMAL", result.message)
        self.assertEqual(len(result.assignments), sum(event.pairs_per_week for event in specs))
        return result.assignments

    def all_days_slots(self):
        times = [("09:00", "10:30"), ("10:40", "12:10"),
                 ("12:20", "13:50"), ("14:30", "16:00")]
        return [timed_slot(day * 4 + i + 1, day, i + 1, start, end)
                for day in range(6) for i, (start, end) in enumerate(times)]

    def test_personal_pd_and_shared_stream_count_actual_attendees_once(self):
        # All three hear the common lecture; only students 1 and 2 have PD,
        # on different days. Merely sharing a group must not copy both projects
        # into every student's distribution profile.
        lecture = ordinary(kind=LessonType.LECTURE, students={1, 2, 3}, groups={1, 2})
        sessions = [fixed_spec(1, 0, 540, 630, {1}),
                    fixed_spec(2, 2, 540, 630, {2})]
        cost = self.cost([lecture], [timed_slot(1, 0, 3)], {(1, 1)}, sessions)
        # Student1:180min Monday ->40; student2:90Mon+90Wed ->20;
        # student3:90Mon ->10; PD teacher:90Mon+90Wed ->20.
        self.assertEqual(cost, 90)

    def test_fixed_pd_weekly_union_avoids_double_counting_alternate_dates(self):
        event = ordinary(students={1})
        sessions = [fixed_spec(1, 0, 540, 630, {1}, dates={date(2026, 9, 7)}),
                    fixed_spec(2, 0, 600, 690, {1}, dates={date(2026, 9, 14)})]
        cost = self.cost([event], [timed_slot(1, 1, 3)], {(1, 1)}, sessions)
        # Union150min Mon,90min Tue: student27+10, teacher27.
        self.assertEqual(cost, 64)

    def test_weekday_distribution_does_not_treat_saturday_as_a_sixth_balancing_day(self):
        cost = self.cost([ordinary()], [timed_slot(1, 5, 3)], {(1, 1)})
        self.assertEqual(cost, 0)

    def test_real_solution_spreads_ten_pairs_over_five_weekdays_without_saturday(self):
        slots = self.all_days_slots()
        result = self.solve([ordinary(pairs=10)], slots)
        by_id = {slot.id: slot for slot in slots}
        loads = Counter(by_id[item["slot_id"]].day_of_week for item in result)
        self.assertEqual(loads.get(5, 0), 0)
        self.assertEqual(sorted(loads.values()), [2, 2, 2, 2, 2])

    def test_smoother_weekday_load_keeps_lecture_applied_blocks_and_all_pairs(self):
        slots = self.all_days_slots()
        specs = [ordinary(1, kind=LessonType.LECTURE), ordinary(2),
                 ordinary(3, kind=LessonType.LECTURE, subject=2),
                 ordinary(4, kind=LessonType.LAB, subject=2)]
        result = self.solve(specs, slots)
        by_id = {slot.id: slot for slot in slots}
        placed = {item["event_id"]: by_id[item["slot_id"]] for item in result}
        for lecture_id, applied_id in ((1, 2), (3, 4)):
            lecture, applied = placed[lecture_id], placed[applied_id]
            self.assertEqual(lecture.day_of_week, applied.day_of_week)
            self.assertEqual(applied.lesson_number, lecture.lesson_number + 1)
        loads = Counter(slot.day_of_week for slot in placed.values())
        self.assertEqual(loads.get(5, 0), 0)
        self.assertEqual(sorted(loads.values()), [2, 2])
        self.assertEqual(specs[-1].lesson_type, LessonType.LAB)
