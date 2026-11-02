from datetime import date, time

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
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
from schedule_generator.services.bells import create_bell_pattern, validate_bell_pattern
from schedule_generator.services.generation import run_generation


class BellScheduleTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.semester = Semester.objects.create(name="Проверка звонков", start_date=date(2026, 9, 1))
        self.group = Group.objects.create(name="251-3210", semester=self.semester)
        Student.objects.create(group=self.group, full_name="Вымышленный студент")
        self.teacher = Teacher.objects.create(full_name="Тестовый преподаватель")
        self.event = LessonEvent.objects.create(
            semester=self.semester, group=self.group, teacher=self.teacher,
            subject=Subject.objects.create(name="Предмет"), lesson_type=LessonType.PRACTICE,
            pairs_per_week=2, audience_size=1)
        Room.objects.create(name="Тестовая аудитория", capacity=30)

    def test_day_seed_has_seven_real_pairs_and_correct_breaks(self):
        pattern = create_bell_pattern(semester=self.semester, code="MOSCOW_DAY")
        self.assertEqual(pattern.slots.count(), 42)
        times = [(s.lesson_number, s.start_time.isoformat(timespec="minutes"),
                  s.end_time.isoformat(timespec="minutes"))
                 for s in pattern.slots.filter(day_of_week=0)]
        self.assertEqual(times, [(1, "09:00", "10:30"), (2, "10:40", "12:10"),
            (3, "12:20", "13:50"), (4, "14:30", "16:00"), (5, "16:10", "17:40"),
            (6, "17:50", "19:20"), (7, "19:30", "21:00")])
        self.assertEqual(pattern.bell_schedule["source_url"],
                         "https://mospolytech.ru/obuchauschimsya/raspisaniya/")

    def test_evening_profile_does_not_reuse_day_times(self):
        pattern = create_bell_pattern(semester=self.semester, code="MOSCOW_EVENING")
        self.assertEqual(pattern.slots.count(), 12)
        self.assertEqual(list(pattern.slots.filter(day_of_week=0).values_list(
            "lesson_number", "start_time", "end_time")),
            [(6, time(18, 20), time(19, 40)), (7, time(19, 50), time(21, 10))])

    def test_declared_profile_rejects_old_synthetic_grid(self):
        pattern = create_bell_pattern(semester=self.semester, code="MOSCOW_DAY")
        pattern.slots.filter(lesson_number=2).update(start_time=time(10, 45), end_time=time(12, 15))
        with self.assertRaisesMessage(ValidationError, "не соответствуют"):
            validate_bell_pattern(pattern, list(pattern.slots.all()),
                                  LessonEvent.objects.filter(pk=self.event.pk))

    def test_solver_and_projection_use_actual_profile_times(self):
        pattern = create_bell_pattern(semester=self.semester, code="MOSCOW_DAY")
        outcome = run_generation(semester=self.semester, week_pattern=pattern,
                                 clear_previous=False, time_limit_seconds=5)
        self.assertEqual(outcome.status, "SOLVED")
        response = self.client.get(f"/api/schedule/schedule/groups/{self.group.pk}/",
                                   {"run_id": outcome.run_id})
        self.assertEqual(response.status_code, 200)
        classes = response.data["scheduled_classes"]
        self.assertEqual(len(classes), 2)
        expected = {(s.start_time.isoformat(), s.end_time.isoformat()) for s in pattern.slots.all()}
        self.assertTrue(all((c["slot_start_time"], c["slot_end_time"]) in expected for c in classes))
        self.assertFalse(any(c["slot_start_time"] == "10:45:00" for c in classes))
        self.assertEqual(GenerationRun.objects.get(pk=outcome.run_id).scheduled_classes.count(), 2)

    def test_used_legacy_slot_cannot_be_retimed_or_deleted(self):
        pattern = WeekPattern.objects.create(semester=self.semester, name="Старый тестовый календарь")
        slot = TimeSlot.objects.create(week_pattern=pattern, day_of_week=1, lesson_number=2,
                                       start_time=time(10, 45), end_time=time(12, 15))
        run = GenerationRun.objects.create(semester=self.semester, status="SOLVED")
        ScheduledClass.objects.create(event=self.event, slot=slot, run=run)
        create_bell_pattern(semester=self.semester, code="MOSCOW_DAY")
        slot.start_time = time(10, 40)
        with self.assertRaisesMessage(ValidationError, "менять нельзя"):
            slot.save()
        slot.refresh_from_db()
        self.assertEqual(slot.start_time, time(10, 45))
        with self.assertRaises(ProtectedError):
            slot.delete()
        user = get_user_model().objects.create_user(username="staff", is_staff=True)
        self.client.force_authenticate(user)
        response = self.client.patch(f"/api/schedule/time-slots/{slot.pk}/",
                                     {"start_time": "10:40"}, format="json")
        self.assertEqual(response.status_code, 400)
        for path in (f"time-slots/{slot.pk}", f"week-patterns/{pattern.pk}"):
            response = self.client.delete(f"/api/schedule/{path}/")
            self.assertEqual(response.status_code, 409)
        self.assertTrue(ScheduledClass.objects.filter(run=run, slot=slot).exists())
        response = self.client.get(f"/api/schedule/schedule/groups/{self.group.pk}/", {"run_id": run.pk})
        self.assertEqual(response.data["scheduled_classes"][0]["slot_start_time"], "10:45:00")

    def test_options_comes_from_prepared_generator_not_rasp(self):
        pattern = create_bell_pattern(semester=self.semester, code="MOSCOW_DAY")
        response = self.client.get("/api/schedule/generation-options/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["authentication_required"])
        item = data["semesters"][0]
        self.assertEqual(item["groups"], [{"id": self.group.pk, "name": self.group.name, "student_count": 1}])
        self.assertEqual(item["patterns"][0]["id"], pattern.pk)
        self.assertEqual(len(item["patterns"][0]["slots"]), 42)
        self.assertEqual(item["teachers"], [{"id": self.teacher.pk, "name": self.teacher.full_name}])
