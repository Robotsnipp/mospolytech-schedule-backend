"""Чтение готового расписания: общие окна (18), свободные аудитории (19),
отчёт по усталости (1) и выборка расписания группы и студента."""

from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from schedule_generator.enums import LessonType
from schedule_generator.models import (
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
    TimeSlot,
    WeekPattern,
)

TUESDAY, THURSDAY = 1, 3
API = "/api/schedule"


class ScheduleReadTestCase(TestCase):
    """Группы A и B, поток лекций для A, ПД для студента B, готовый запуск."""

    @classmethod
    def setUpTestData(cls):
        cls.semester = Semester.objects.create(name="Q", start_date=date(2026, 9, 1))
        pattern = WeekPattern.objects.create(semester=cls.semester)
        cls.slots = {}
        times = {1: (time(9, 0), time(10, 30)), 2: (time(10, 40), time(12, 10)),
                 3: (time(12, 20), time(13, 50))}
        for lesson_number, (start, end) in times.items():
            cls.slots[(TUESDAY, lesson_number)] = TimeSlot.objects.create(
                week_pattern=pattern, day_of_week=TUESDAY, lesson_number=lesson_number,
                start_time=start, end_time=end)
        cls.slots[(THURSDAY, 1)] = TimeSlot.objects.create(
            week_pattern=pattern, day_of_week=THURSDAY, lesson_number=1)

        subject = Subject.objects.create(name="Math")
        cls.group_a = Group.objects.create(name="261-101")
        cls.group_b = Group.objects.create(name="261-102")
        cls.student_a = Student.objects.create(group=cls.group_a, full_name="А")
        cls.student_b = Student.objects.create(group=cls.group_b, full_name="Б")
        cls.solved_run = GenerationRun.objects.create(semester=cls.semester, status="SOLVED")
        cls.room_small = Room.objects.create(name="R1", capacity=30)
        cls.room_big = Room.objects.create(name="R2", capacity=60)
        Room.objects.create(name="R3", capacity=60, is_active=False)

        def event(**kwargs):
            return LessonEvent.objects.create(
                semester=cls.semester, subject=subject, pairs_per_week=1, **kwargs)

        def put(event_obj, day, lesson_number, room=None):
            ScheduledClass.objects.create(event=event_obj, slot=cls.slots[(day, lesson_number)],
                                          room=room, run=cls.solved_run)

        put(event(group=cls.group_a, lesson_type=LessonType.PRACTICE), TUESDAY, 1, cls.room_small)
        put(event(group=cls.group_b, lesson_type=LessonType.PRACTICE), TUESDAY, 2, cls.room_big)
        stream = LectureStream.objects.create(semester=cls.semester, subject=subject)
        stream.groups.add(cls.group_a)
        cls.lecture = event(stream=stream, lesson_type=LessonType.LECTURE)
        put(cls.lecture, THURSDAY, 1)
        cls.project = Project.objects.create(semester=cls.semester, title="P")
        ProjectEnrollment.objects.create(student=cls.student_b, project=cls.project)
        cls.pd_event = event(project=cls.project, lesson_type=LessonType.PD)

    def setUp(self):
        self.client = APIClient()

    def get(self, path, **params):
        return self.client.get(f"{API}/{path}", {"semester": self.semester.pk, **params})


class GroupAndStudentScheduleTests(ScheduleReadTestCase):
    def test_group_schedule_includes_lectures_of_its_streams(self):
        response = self.get(f"schedule/groups/{self.group_a.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual({item["event"] for item in response.json()["scheduled_classes"]},
                         set(ScheduledClass.objects.filter(
                             event__group=self.group_a).values_list("event_id", flat=True))
                         | {self.lecture.pk})

    def test_group_without_stream_gets_only_its_own_pairs(self):
        response = self.get(f"schedule/groups/{self.group_b.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["scheduled_classes"]), 1)

    def test_student_schedule_works_with_and_without_project(self):
        self.assertEqual(self.get(f"schedule/students/{self.student_a.pk}/").status_code, 200)
        self.assertEqual(self.get(f"schedule/students/{self.student_b.pk}/").status_code, 200)

    def test_student_schedule_follows_project_pairs(self):
        ScheduledClass.objects.create(event=self.pd_event, slot=self.slots[(TUESDAY, 3)],
                                      room=self.room_big, run=self.solved_run)
        events = {item["event"] for item in
                  self.get(f"schedule/students/{self.student_b.pk}/").json()["scheduled_classes"]}
        self.assertIn(self.pd_event.pk, events)
        events_a = {item["event"] for item in
                    self.get(f"schedule/students/{self.student_a.pk}/").json()["scheduled_classes"]}
        self.assertNotIn(self.pd_event.pk, events_a)


class CommonFreeSlotsTests(ScheduleReadTestCase):
    def free(self, *students):
        response = self.get("schedule/common-free-slots/",
                            students=",".join(str(s.pk) for s in students))
        self.assertEqual(response.status_code, 200, response.content)
        return {(item["day_of_week"], item["lesson_number"]) for item in response.json()}

    def test_one_student_sees_own_free_slots(self):
        # студент A занят: вт-1 (своя пара) и чт-1 (лекция потока); свободны вт-2, вт-3
        self.assertEqual(self.free(self.student_a), {(TUESDAY, 2), (TUESDAY, 3)})

    def test_slots_must_be_free_for_everyone(self):
        # студент B занят вт-2; вместе свободен только вт-3, а вт-1 и чт-1 заняты A
        self.assertEqual(self.free(self.student_a, self.student_b), {(TUESDAY, 3)})

    def test_project_pairs_make_a_student_busy(self):
        ScheduledClass.objects.create(event=self.pd_event, slot=self.slots[(TUESDAY, 3)],
                                      room=self.room_big, run=self.solved_run)
        self.assertEqual(self.free(self.student_a, self.student_b), set())

    def test_without_a_run_everything_is_free(self):
        empty = Semester.objects.create(name="Empty", start_date=date(2026, 9, 1))
        pattern = WeekPattern.objects.create(semester=empty)
        TimeSlot.objects.create(week_pattern=pattern, day_of_week=TUESDAY, lesson_number=1)
        response = self.client.get(f"{API}/schedule/common-free-slots/",
                                   {"semester": empty.pk, "students": self.student_a.pk})
        self.assertEqual(len(response.json()), 1)

    def test_input_is_validated(self):
        for students in ("", "x", "999999"):
            with self.subTest(students=students):
                response = self.get("schedule/common-free-slots/", students=students)
                self.assertEqual(response.status_code, 400)
        too_many = ",".join(str(n) for n in range(1, 25))
        self.assertEqual(self.get("schedule/common-free-slots/", students=too_many).status_code,
                         400)


class FreeRoomsTests(ScheduleReadTestCase):
    def names(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return [room["name"] for room in response.json()["rooms"]]

    def test_occupied_and_inactive_rooms_are_excluded(self):
        self.assertEqual(self.names(self.get("schedule/free-rooms/", day_of_week=TUESDAY,
                                             lesson_number=1)), ["R2"])
        self.assertEqual(self.names(self.get("schedule/free-rooms/", day_of_week=TUESDAY,
                                             lesson_number=3)), ["R1", "R2"])

    def test_capacity_filter(self):
        self.assertEqual(self.names(self.get("schedule/free-rooms/", day_of_week=TUESDAY,
                                             lesson_number=3, min_capacity=40)), ["R2"])

    def test_moment_is_resolved_in_university_time(self):
        # 2026-10-20 — вторник; в 09:30 по Москве идёт 1-я пара (R1 занята)
        self.assertEqual(date(2026, 10, 20).weekday(), TUESDAY)
        naive = self.get("schedule/free-rooms/", at="2026-10-20T09:30:00")
        with_zone = self.get("schedule/free-rooms/", at="2026-10-20T06:30:00Z")
        self.assertEqual(self.names(naive), ["R2"])
        self.assertEqual(self.names(with_zone), ["R2"])
        self.assertEqual(naive.json()["slot"]["lesson_number"], 1)

    def test_no_pair_at_the_moment(self):
        response = self.get("schedule/free-rooms/", at="2026-10-20T23:00:00")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rooms"], [])
        self.assertIsNone(response.json()["slot"])

    def test_bad_parameters(self):
        self.assertEqual(self.get("schedule/free-rooms/", day_of_week=1).status_code, 400)
        self.assertEqual(self.get("schedule/free-rooms/", day_of_week=1, lesson_number=1,
                                  at="2026-10-20T09:30:00").status_code, 400)
        self.assertEqual(self.get("schedule/free-rooms/", day_of_week=1,
                                  lesson_number=9).status_code, 400)


class WellbeingReportTests(ScheduleReadTestCase):
    def test_report_matches_formula_and_counts_stream_groups(self):
        # группа A: вт 1, 2, 3 (усталость 35, окон нет) и чт-1 (лекция её потока)
        for lesson_number in (2, 3):
            event = LessonEvent.objects.create(
                semester=self.semester, subject=Subject.objects.first(), group=self.group_a,
                lesson_type=LessonType.PRACTICE, pairs_per_week=1)
            ScheduledClass.objects.create(event=event, slot=self.slots[(TUESDAY, lesson_number)],
                                          run=self.solved_run)
        admin = get_user_model().objects.create_user("admin", password="x", is_staff=True)
        self.client.force_authenticate(admin)
        response = self.client.get(f"{API}/runs/{self.solved_run.pk}/wellbeing/")
        self.assertEqual(response.status_code, 200, response.content)
        report = response.json()
        group_a = next(group for group in report["groups"] if group["name"] == "261-101")
        days = {day["day_of_week"]: day for day in group_a["days"]}
        self.assertEqual(days[TUESDAY]["pairs"], 3)
        self.assertEqual(days[TUESDAY]["fatigue"], 35)      # 3 пары + ранний старт
        self.assertEqual(days[TUESDAY]["windows"], 0)
        self.assertFalse(days[TUESDAY]["overloaded"])
        self.assertEqual(days[THURSDAY]["pairs"], 1)
        self.assertEqual(days[THURSDAY]["fatigue"], 15)     # одна пара + ранний старт
        self.assertEqual(report["summary"]["groups"], 2)
        self.assertEqual(report["comfort_level"], 40)

    def test_report_requires_admin(self):
        self.assertIn(self.client.get(f"{API}/runs/{self.solved_run.pk}/wellbeing/").status_code,
                      (401, 403))
