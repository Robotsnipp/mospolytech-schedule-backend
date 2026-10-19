"""Самовосстановление расписания (фича 14) на реальном решателе CP-SAT."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from schedule_generator.enums import LessonType
from schedule_generator.models import (
    GenerationRun,
    Group,
    LessonEvent,
    Room,
    ScheduledClass,
    Semester,
    Student,
    Subject,
    Teacher,
    TimeSlot,
    WeekPattern,
)
from schedule_generator.services.generation import run_generation
from schedule_generator.services.replan import ReplanError, replan_after_disruption

TUESDAY, THURSDAY = 1, 3


class ReplanTestCase(TestCase):
    """Две группы, один общий преподаватель, по шесть слотов в неделю: места хватает,
    поэтому все не затронутые сбоем пары должны остаться на своих местах."""

    def setUp(self):
        self.semester = Semester.objects.create(name="R", start_date=date(2026, 9, 1))
        pattern = WeekPattern.objects.create(semester=self.semester)
        for day in (TUESDAY, THURSDAY):
            for lesson_number in (2, 3, 4):
                TimeSlot.objects.create(week_pattern=pattern, day_of_week=day,
                                        lesson_number=lesson_number)
        subject = Subject.objects.create(name="Math")
        self.group_a = Group.objects.create(name="261-101")
        self.group_b = Group.objects.create(name="261-102")
        self.teacher = Teacher.objects.create(full_name="Иванов")
        self.rooms = [Room.objects.create(name=f"R{i}", capacity=30) for i in range(3)]
        self.teacher_events = [self.event(subject, self.group_a, self.teacher),
                               self.event(subject, self.group_b, self.teacher)]
        self.event(subject, self.group_a)
        self.event(subject, self.group_b)
        outcome = run_generation(semester=self.semester, time_limit_seconds=5)
        self.assertEqual(outcome.status, "SOLVED")
        self.run_obj = GenerationRun.objects.get(pk=outcome.run_id)

    def event(self, subject, group, teacher=None):
        return LessonEvent.objects.create(
            semester=self.semester, subject=subject, group=group, teacher=teacher,
            lesson_type=LessonType.PRACTICE, pairs_per_week=1)

    def placements(self, run):
        return {(c.event_id, c.slot_id, c.room_id)
                for c in ScheduledClass.objects.filter(run=run)}


class TeacherDisruptionTests(ReplanTestCase):
    def setUp(self):
        super().setUp()
        first_class = ScheduledClass.objects.filter(
            run=self.run_obj, event=self.teacher_events[0]).select_related("slot").get()
        self.sick_day = first_class.slot.day_of_week

    def test_teacher_pairs_leave_the_unavailable_day(self):
        outcome = replan_after_disruption(
            run=self.run_obj, teacher=self.teacher, unavailable_days=[self.sick_day],
            time_limit_seconds=5)
        self.assertEqual(outcome.status, "SOLVED")
        teacher_days = ScheduledClass.objects.filter(
            run_id=outcome.run_id, event__teacher=self.teacher
        ).values_list("slot__day_of_week", flat=True)
        self.assertEqual(len(teacher_days), 2)
        self.assertNotIn(self.sick_day, teacher_days)

    def test_untouched_pairs_stay_exactly_where_they_were(self):
        old = ScheduledClass.objects.filter(run=self.run_obj).select_related("slot")
        affected = {c.id for c in old
                    if c.event.teacher_id == self.teacher.id
                    and c.slot.day_of_week == self.sick_day}
        outcome = replan_after_disruption(
            run=self.run_obj, teacher=self.teacher, unavailable_days=[self.sick_day],
            time_limit_seconds=5)
        kept = {(c.event_id, c.slot_id, c.room_id) for c in old if c.id not in affected}
        self.assertLessEqual(kept, self.placements(outcome.run_id))
        self.assertEqual(outcome.affected_count, len(affected))
        self.assertEqual(outcome.unchanged_count, old.count() - len(affected))

    def test_source_run_is_not_modified_and_new_run_is_recorded(self):
        before = self.placements(self.run_obj)
        outcome = replan_after_disruption(
            run=self.run_obj, teacher=self.teacher, unavailable_days=[self.sick_day],
            time_limit_seconds=5)
        self.assertEqual(self.placements(self.run_obj), before)
        new_run = GenerationRun.objects.get(pk=outcome.run_id)
        self.assertNotEqual(new_run.pk, self.run_obj.pk)
        self.assertIn(f"#{self.run_obj.pk}", new_run.message)

    def test_changes_report_removed_and_added_placements(self):
        outcome = replan_after_disruption(
            run=self.run_obj, teacher=self.teacher, unavailable_days=[self.sick_day],
            time_limit_seconds=5)
        self.assertTrue(outcome.changes)
        for change in outcome.changes:
            self.assertTrue(change["removed"])
            self.assertTrue(change["added"])
            self.assertTrue(all(item["day_of_week"] != self.sick_day for item in change["added"]))
        self.assertGreaterEqual(outcome.affected_students, 0)


class RoomDisruptionTests(ReplanTestCase):
    def test_closed_room_is_no_longer_used_and_the_rest_is_stable(self):
        old = list(ScheduledClass.objects.filter(run=self.run_obj))
        closed = next(c.room for c in old if c.room_id is not None)
        affected = {c.id for c in old if c.room_id == closed.id}
        outcome = replan_after_disruption(run=self.run_obj, room=closed, time_limit_seconds=5)
        self.assertEqual(outcome.status, "SOLVED")
        new = ScheduledClass.objects.filter(run_id=outcome.run_id)
        self.assertFalse(new.filter(room=closed).exists())
        kept = {(c.event_id, c.slot_id, c.room_id) for c in old if c.id not in affected}
        self.assertLessEqual(kept, self.placements(outcome.run_id))


class ReplanValidationTests(ReplanTestCase):
    def test_exactly_one_disruption_is_required(self):
        with self.assertRaises(ReplanError):
            replan_after_disruption(run=self.run_obj)
        with self.assertRaises(ReplanError):
            replan_after_disruption(run=self.run_obj, teacher=self.teacher,
                                    unavailable_days=[1], room=self.rooms[0])

    def test_teacher_needs_days_and_days_must_be_valid(self):
        with self.assertRaises(ReplanError):
            replan_after_disruption(run=self.run_obj, teacher=self.teacher)
        with self.assertRaises(ReplanError):
            replan_after_disruption(run=self.run_obj, teacher=self.teacher,
                                    unavailable_days=[9])

    def test_disruption_that_touches_nothing_is_rejected(self):
        with self.assertRaises(ReplanError):
            replan_after_disruption(run=self.run_obj, teacher=self.teacher,
                                    unavailable_days=[5])   # суббота: слотов нет

    def test_failed_run_cannot_be_replanned(self):
        self.run_obj.status = "ERROR"
        self.run_obj.save()
        with self.assertRaises(ReplanError):
            replan_after_disruption(run=self.run_obj, teacher=self.teacher,
                                    unavailable_days=[1])


class ReplanApiTests(ReplanTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.url = "/api/schedule/replan/"
        sick = ScheduledClass.objects.filter(
            run=self.run_obj, event__teacher=self.teacher).select_related("slot").first()
        self.payload = {"run": self.run_obj.pk, "teacher": self.teacher.pk,
                        "unavailable_days": [sick.slot.day_of_week], "time_limit_seconds": 5}

    def test_requires_admin(self):
        self.assertIn(self.client.post(self.url, self.payload, format="json").status_code,
                      (401, 403))
        user = get_user_model().objects.create_user("plain", password="x")
        self.client.force_authenticate(user)
        self.assertEqual(self.client.post(self.url, self.payload, format="json").status_code, 403)

    def test_admin_gets_new_run_and_changes(self):
        admin = get_user_model().objects.create_user("admin", password="x", is_staff=True)
        self.client.force_authenticate(admin)
        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertNotEqual(body["run"], self.run_obj.pk)
        self.assertEqual(body["status"], "SOLVED")
        self.assertTrue(body["changes"])

    def test_bad_request_is_400_with_reason(self):
        admin = get_user_model().objects.create_user("admin", password="x", is_staff=True)
        self.client.force_authenticate(admin)
        response = self.client.post(
            self.url, {"run": self.run_obj.pk, "teacher": self.teacher.pk}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.json())

    def test_student_count_uses_students_of_changed_groups(self):
        Student.objects.create(group=self.group_a, full_name="А")
        Student.objects.create(group=self.group_b, full_name="Б")
        outcome = replan_after_disruption(
            run=self.run_obj, teacher=self.teacher,
            unavailable_days=self.payload["unavailable_days"], time_limit_seconds=5)
        self.assertGreaterEqual(outcome.affected_students, 1)
