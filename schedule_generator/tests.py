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
    LectureStream,
    LessonEvent,
    Module,
    Room,
    ScheduledClass,
    Semester,
    Student,
    Subject,
    Teacher,
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
        # вт: пн/ср зарезервированы под ПД, обычные пары там не планируются
        # (см. services/day_rules.py)
        self.slot = TimeSlot.objects.create(week_pattern=self.pattern, day_of_week=1, lesson_number=1)
        self.subject = Subject.objects.create(name='Math')
        self.group = Group.objects.create(name='261-101')
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

    def test_group_name_formats_regular_and_dpo(self):
        """Обычные группы — формат «241-334», группы ДПО — свой формат «ДПО-...»."""
        # обычная группа: валидный формат, год зачисления считается из названия
        regular = Group(name='241-334')
        regular.full_clean()
        self.assertEqual(regular.admission_year, 2024)
        # невалидное имя обычной группы отклоняется
        with self.assertRaises(ValidationError):
            Group(name='G').full_clean()
        with self.assertRaises(ValidationError):
            Group(name='2413345678').full_clean()
        # группа ДПО: свой формат, год зачисления не выводится и не заполняется
        dpo = Group(name='ДПО-2026-07', is_dpo=True)
        dpo.full_clean()
        self.assertIsNone(dpo.admission_year)
        Group.objects.create(name='ДПО Python', is_dpo=True)
        # обычному формату группа ДПО не следует, а ДПО-имени — обычная группа
        with self.assertRaises(ValidationError):
            Group(name='241-334', is_dpo=True).full_clean()
        with self.assertRaises(ValidationError):
            Group(name='Группа слушателей').full_clean()

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
        # вторая аудитория и второй слот (чт): иначе решателю физически негде
        # поставить вторую очную пару (одна аудитория/один слот -> PARTIAL)
        Room.objects.create(name='R2', capacity=30)
        TimeSlot.objects.create(week_pattern=self.pattern, day_of_week=3, lesson_number=1)
        result = run_generation(semester=self.semester, time_limit_seconds=2)
        self.assertEqual(result.status, 'SOLVED')
        classes = ScheduledClass.objects.filter(run_id=result.run_id)
        first_class = classes.get(event=first_event)
        second_class = classes.get(event=second_event)
        # инвариант решателя: в одном слоте аудитория занята не более чем одной парой
        self.assertFalse(first_class.slot_id == second_class.slot_id
                         and first_class.room_id == second_class.room_id)


class LectureOnsiteGapTests(TestCase):
    """Лекция и очное занятие одного участника в один день — только через
    пропуск; при этом решатель старается делать пропусков как можно меньше."""

    def setUp(self):
        self.semester = Semester.objects.create(name='GapTest', start_date=date(2026, 9, 1))
        self.pattern = WeekPattern.objects.create(semester=self.semester)
        # вторник: три пары подряд (слоты нумеруются lesson_number внутри дня)
        self.slots = [
            TimeSlot.objects.create(week_pattern=self.pattern,
                                     day_of_week=1, lesson_number=number)
            for number in (1, 2, 3)
        ]
        self.subject = Subject.objects.create(name='Math')
        self.group = Group.objects.create(name='261-101')
        Student.objects.create(group=self.group, full_name='Студент')
        self.room = Room.objects.create(name='R', capacity=30)
        self.teacher = Teacher.objects.create(full_name='Преподаватель')
        self.stream = LectureStream.objects.create(
            semester=self.semester, subject=self.subject, teacher=self.teacher)
        self.stream.groups.add(self.group)

    def lecture_event(self):
        return LessonEvent.objects.create(
            semester=self.semester, subject=self.subject,
            stream=self.stream, teacher=self.teacher,
            lesson_type=LessonType.LECTURE, pairs_per_week=1)

    def onsite_event(self):
        return LessonEvent.objects.create(
            semester=self.semester, subject=self.subject,
            group=self.group, teacher=self.teacher,
            lesson_type=LessonType.PRACTICE, pairs_per_week=1)

    def test_lecture_and_onsite_never_adjacent_same_day(self):
        """Жёсткое правило: между лекцией и очным занятием группы всегда
        есть хотя бы одна пустая пара (даже когда их больше двух за день)."""
        self.lecture_event()
        self.lecture_event()
        self.onsite_event()
        result = run_generation(semester=self.semester, time_limit_seconds=5)
        self.assertEqual(result.status, 'SOLVED')
        classes = list(ScheduledClass.objects.filter(run_id=result.run_id)
                       .order_by('slot__lesson_number'))
        by_slot = {c.slot_id: c.event.lesson_type for c in classes}
        ordered_slots = [(s.id, s.lesson_number) for s in self.slots]
        for (slot_a, num_a), (slot_b, num_b) in zip(ordered_slots, ordered_slots[1:]):
            kinds = {by_slot.get(slot_a), by_slot.get(slot_b)}
            # соседние по номеру слоты не могут занять лекция и очное одновременно
            if num_b == num_a + 1:
                self.assertFalse(
                    LessonType.LECTURE in kinds
                    and kinds - {LessonType.LECTURE, None},
                    f'Лекция и очное стоят подряд в парах {num_a} и {num_b}')

    def test_exactly_one_gap_between_lecture_and_onsite(self):
        """Минимизация пропусков: лекция и очное разнесены ровно одной
        пустой парой, а не разбросаны по всему дню."""
        lecture = self.lecture_event()
        onsite = self.onsite_event()
        result = run_generation(semester=self.semester, time_limit_seconds=5)
        self.assertEqual(result.status, 'SOLVED')
        classes = ScheduledClass.objects.filter(run_id=result.run_id)
        lecture_slot = classes.get(event=lecture).slot.lesson_number
        onsite_slot = classes.get(event=onsite).slot.lesson_number
        self.assertEqual(abs(lecture_slot - onsite_slot), 2)
