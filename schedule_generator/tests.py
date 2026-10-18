from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from schedule_generator.api.serializers import GenerateRequestSerializer
from schedule_generator.enums import Format, LessonType
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
    Student,
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

    def dpo_event(self, group, lesson_format):
        return LessonEvent.objects.create(
            semester=self.semester, subject=self.subject, group=group,
            lesson_type=LessonType.DPO, format=lesson_format, pairs_per_week=1,
        )

    def test_dpo_events_require_own_dpo_group(self):
        """ДПО планируется только на отдельную группу ДПО."""
        dpo_group = Group.objects.create(name='ДПО-Тест', is_dpo=True)
        Student.objects.create(group=dpo_group, full_name='Слушатель')
        self.dpo_event(dpo_group, Format.ONLINE).full_clean()
        self.dpo_event(dpo_group, Format.ONSITE).full_clean()
        # обычная учебная группа для ДПО не подходит
        with self.assertRaises(ValidationError):
            self.dpo_event(self.group, Format.ONLINE).full_clean()
        # занятие другого типа на группу ДПО планировать нельзя
        with self.assertRaises(ValidationError):
            LessonEvent.objects.create(
                semester=self.semester, subject=self.subject, group=dpo_group,
                lesson_type=LessonType.PRACTICE,
            ).full_clean()

    def test_dpo_generation_online_and_onsite_for_separate_groups(self):
        """Пары ДПО (онлайн и очные) планируются на отдельные группы ДПО
        реальным решателем; очная пара занимает аудиторию, онлайн — нет."""
        first = Group.objects.create(name='ДПО-1', is_dpo=True)
        second = Group.objects.create(name='ДПО-2', is_dpo=True)
        online_event = self.dpo_event(first, Format.ONLINE)
        onsite_event = self.dpo_event(second, Format.ONSITE)
        result = run_generation(semester=self.semester, time_limit_seconds=2)
        self.assertEqual(result.status, 'SOLVED')
        classes = ScheduledClass.objects.filter(run_id=result.run_id)
        online_classes = [c for c in classes if c.event_id == online_event.id]
        onsite_classes = [c for c in classes if c.event_id == onsite_event.id]
        self.assertEqual(len(online_classes), 1)
        self.assertEqual(len(onsite_classes), 1)
        self.assertIsNone(online_classes[0].room_id)
        self.assertEqual(onsite_classes[0].room_id, self.room.id)

    def test_dpo_two_onsite_groups_do_not_share_room_slot(self):
        """Две очные группы ДПО не могут занять одну аудиторию в одном слоте."""
        first = Group.objects.create(name='ДПО-1', is_dpo=True)
        second = Group.objects.create(name='ДПО-2', is_dpo=True)
        first_event = self.dpo_event(first, Format.ONSITE)
        second_event = self.dpo_event(second, Format.ONSITE)
        # вторая аудитория и второй слот: иначе решателю физически негде
        # поставить вторую очную пару (одна аудитория/один слот -> PARTIAL)
        Room.objects.create(name='R2', capacity=30)
        TimeSlot.objects.create(week_pattern=self.pattern, day_of_week=1, lesson_number=1)
        result = run_generation(semester=self.semester, time_limit_seconds=2)
        self.assertEqual(result.status, 'SOLVED')
        classes = ScheduledClass.objects.filter(run_id=result.run_id)
        first_class = classes.get(event=first_event)
        second_class = classes.get(event=second_event)
        # инвариант решателя: в одном слоте аудитория занята не более чем одной парой
        self.assertFalse(first_class.slot_id == second_class.slot_id
                         and first_class.room_id == second_class.room_id)


class ShortageDiagnosticsTests(TestCase):
    """Объяснения «чего не хватает» при PARTIAL/INFEASIBLE (diagnostics)."""

    def setUp(self):
        self.semester = Semester.objects.create(name='Diag', start_date=date(2026, 9, 1))
        self.pattern = WeekPattern.objects.create(semester=self.semester)
        # обычные пары идут во вт/чт/пт/сб — заведём только вторник
        self.slot = TimeSlot.objects.create(week_pattern=self.pattern, day_of_week=1, lesson_number=1)
        self.subject = Subject.objects.create(name='Math')
        self.group = Group.objects.create(name='G')
        self.room = Room.objects.create(name='R', capacity=30)

    def test_partial_issue_explains_missing_slots(self):
        """PARTIAL: в issue попадает число недостающих пар и причина-дефицит."""
        LessonEvent.objects.create(
            semester=self.semester, subject=self.subject, group=self.group,
            lesson_type=LessonType.PRACTICE, pairs_per_week=2)
        result = run_generation(semester=self.semester, time_limit_seconds=2)
        self.assertEqual(result.status, 'PARTIAL')
        run = GenerationRun.objects.get(pk=result.run_id)
        texts = list(run.issues.values_list('text', flat=True))
        joined = '\n'.join(texts)
        self.assertIn('не хватает 1 пару', joined)
        self.assertIn('не хватает свободных слотов', joined)
        self.assertTrue(any(t.startswith('Чего не хватает:') for t in texts))

    def test_partial_issue_explains_room_capacity(self):
        """PARTIAL: очной паре некуда встать — текст про вместимость аудиторий."""
        self.room.capacity = 10
        self.room.save()
        event = LessonEvent.objects.create(
            semester=self.semester, subject=self.subject, group=self.group,
            lesson_type=LessonType.PRACTICE, audience_size=50)
        Student.objects.bulk_create(
            [Student(group=self.group, full_name=f'S{i}') for i in range(50)])
        result = run_generation(semester=self.semester, time_limit_seconds=2)
        self.assertEqual(result.status, 'PARTIAL')
        run = GenerationRun.objects.get(pk=result.run_id)
        issue_text = run.issues.filter(event=event).first().text
        self.assertIn('нет активной аудитории, подходящей по вместимости', issue_text)
