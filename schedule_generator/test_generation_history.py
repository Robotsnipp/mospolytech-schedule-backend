from datetime import date, time

from django.test import TestCase
from rest_framework.test import APIClient

from .models import (
    GenerationRun,
    Group,
    LessonEvent,
    ScheduledClass,
    Semester,
    Subject,
    Teacher,
    TimeSlot,
    WeekPattern,
)
from .services.replan import ReplanError, replan_after_disruption


class GenerationHistoryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        semester = Semester.objects.create(name="История", start_date=date(2026, 9, 1))
        self.group = Group.objects.create(name="251-3210", semester=semester)
        self.teacher = Teacher.objects.create(full_name="Исходный преподаватель")
        self.event = LessonEvent.objects.create(
            semester=semester, group=self.group, teacher=self.teacher,
            subject=Subject.objects.create(name="Исходная дисциплина"), lesson_type="PRACTICE")
        pattern = WeekPattern.objects.create(semester=semester)
        slot = TimeSlot.objects.create(week_pattern=pattern, day_of_week=0, lesson_number=1,
                                       start_time=time(9), end_time=time(10, 30))
        self.run = GenerationRun.objects.create(
            semester=semester, status="SOLVED", settings_snapshot={"schema_version": 1},
            input_snapshot={"schema_version": 1, "group_id": self.group.pk, "week_pattern_id": pattern.pk,
                            "events": [{"id": self.event.pk, "subject_name": "Исходная дисциплина",
                                        "lesson_type": "PRACTICE", "format": "ONSITE", "teacher_id": self.teacher.pk,
                                        "teacher_name": self.teacher.full_name, "group_id": self.group.pk,
                                        "group_name": self.group.name, "blocking_group_ids": [self.group.pk]}],
                            "slots": [{"id": slot.pk, "day_of_week": 0, "lesson_number": 1,
                                       "start_time": "09:00", "end_time": "10:30"}], "rooms": []})
        self.scheduled = ScheduledClass.objects.create(run=self.run, event=self.event, slot=slot)

    def test_public_history_has_old_unknown_settings_and_new_snapshots(self):
        old = GenerationRun.objects.create(semester=self.run.semester, status="SOLVED")
        response = self.client.get("/api/schedule/generation-history/")
        self.assertEqual(response.status_code, 200)
        items = {item["id"]: item for item in response.data}
        self.assertFalse(items[old.pk]["editable"])
        self.assertTrue(items[self.run.pk]["editable"])
        self.assertEqual(self.client.get(f"/api/schedule/generation-history/{old.pk}/").data["settings_snapshot"], {})
        self.assertEqual(self.client.get("/api/schedule/generation-history/9999/").status_code, 404)
        self.assertEqual(self.client.get("/api/schedule/generation-history/?semester=bad").status_code, 400)

    def test_snapshot_labels_and_teacher_membership_survive_catalog_edits(self):
        changed = Teacher.objects.create(full_name="Новый преподаватель")
        self.event.teacher = changed
        self.event.subject.name = "Новое название"
        self.event.subject.save()
        self.event.save()
        response = self.client.get(f"/api/schedule/generation-history/{self.run.pk}/")
        row = response.data["scheduled_classes"][0]
        self.assertEqual(row["subject_name"], "Исходная дисциплина")
        self.assertEqual(row["teacher_id"], self.teacher.pk)
        projection = self.client.get(f"/api/schedule/schedule/teachers/{self.teacher.pk}/?run_id={self.run.pk}")
        self.assertEqual(len(projection.data["scheduled_classes"]), 1)

    def test_direct_legacy_replan_cannot_reset_new_settings(self):
        with self.assertRaises(ReplanError):
            replan_after_disruption(run=self.run, teacher=self.teacher, unavailable_days=[0])
        self.assertEqual(GenerationRun.objects.count(), 1)
