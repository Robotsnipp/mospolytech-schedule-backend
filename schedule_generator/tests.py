from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from schedule_generator.api.serializers import GenerateRequestSerializer
from schedule_generator.enums import LessonType
from schedule_generator.models import (
    Curriculum,
    CurriculumEntry,
    GenerationRun,
    Group,
    LessonEvent,
    Module,
    Room,
    ScheduledClass,
    Semester,
    Subject,
    TimeSlot,
    WeekPattern,
)
from schedule_generator.services.generation import GenerationError, run_generation


class PlanningTests(TestCase):
    def setUp(self):
        self.semester = Semester.objects.create(name='Test', start_date=date(2026, 9, 1))
        self.module = Module.objects.create(semester=self.semester, number=1, start_date=date(2026, 9, 1), end_date=date(2026, 10, 31), teaching_weeks=9)
        self.other_module = Module.objects.create(semester=self.semester, number=2, start_date=date(2026, 11, 1), end_date=date(2026, 12, 31), teaching_weeks=9)
        self.pattern = WeekPattern.objects.create(semester=self.semester)
        self.slot = TimeSlot.objects.create(week_pattern=self.pattern, day_of_week=0, lesson_number=1)
        self.subject = Subject.objects.create(name='Math')
        self.group = Group.objects.create(name='G')
        self.room = Room.objects.create(name='R', capacity=30)

    def event(self, module=None):
        return LessonEvent.objects.create(semester=self.semester, subject=self.subject, group=self.group, lesson_type=LessonType.PRACTICE, module=module)

    def test_legacy_generation_and_retained_versions(self):
        event = self.event()
        first = run_generation(semester=self.semester, clear_previous=False, time_limit_seconds=2)
        second = run_generation(semester=self.semester, clear_previous=False, time_limit_seconds=2)
        self.assertEqual(first.status, 'SOLVED')
        self.assertEqual(second.status, 'SOLVED')
        self.assertEqual(ScheduledClass.objects.filter(event=event).count(), 2)

    def test_module_filters_events_and_inactive_rooms(self):
        wanted = self.event(self.module)
        self.event(self.other_module)
        self.room.is_active = False
        self.room.save()
        active = Room.objects.create(name='Active', capacity=30)
        result = run_generation(semester=self.semester, module=self.module, time_limit_seconds=2)
        classes = ScheduledClass.objects.filter(run_id=result.run_id)
        self.assertEqual(result.status, 'SOLVED')
        self.assertEqual(list(classes.values_list('event_id', 'room_id')), [(wanted.id, active.id)])
        with self.assertRaises(GenerationError):
            run_generation(semester=self.semester)

    def test_partial_does_not_delete_previous_schedule(self):
        self.event(self.module)
        first = run_generation(semester=self.semester, module=self.module, time_limit_seconds=2)
        self.room.is_active = False
        self.room.save()
        second = run_generation(semester=self.semester, module=self.module, time_limit_seconds=2)
        self.assertEqual(second.status, 'PARTIAL')
        self.assertTrue(ScheduledClass.objects.filter(run_id=first.run_id).exists())

    def test_invalid_module_rejected_by_service_and_api(self):
        other = Semester.objects.create(name='Other', start_date=date(2026, 9, 1))
        with self.assertRaises(GenerationError):
            run_generation(semester=other, module=self.module)
        serializer = GenerateRequestSerializer(data={'semester': other.id, 'module': self.module.id})
        self.assertFalse(serializer.is_valid())
        self.assertIn('module', serializer.errors)
        self.assertEqual(GenerationRun.objects.count(), 0)

    def test_module_date_constraint(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Module.objects.create(semester=self.semester, number=3, start_date=date(2026, 12, 1), end_date=date(2026, 11, 1), teaching_weeks=1)

    def test_curriculum_form_and_subject_validation(self):
        values = dict(name='Web', program_code='09.03.01', profile='Web', admission_year=2025, duration_months=48)
        plan = Curriculum.objects.create(**values, study_form='FULL_TIME')
        Curriculum.objects.create(**values, study_form='EXTRAMURAL')
        self.group.curriculum = plan
        self.group.save()
        entry = CurriculumEntry.objects.create(curriculum=plan, subject=self.subject, discipline_code='B1', semester_number=1, practice_hours=18, has_credit=True)
        event = self.event(self.module)
        event.curriculum_entry = entry
        event.full_clean()
        event.subject = Subject.objects.create(name='Other subject')
        with self.assertRaises(ValidationError):
            event.full_clean()
