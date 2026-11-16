"""Демо одной командой: реальный CP-SAT, полнота, ограничения и пересоздание."""

from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from schedule_generator.models import (
    DPOMembership,
    GenerationIssue,
    GenerationRun,
    Location,
    ScheduledClass,
    Semester,
    Teacher,
)
from schedule_generator.services.schedule_queries import student_holder_filter


class SeedDemoTests(TestCase):
    def seed(self, *args):
        out = StringIO()
        call_command("seed_demo", "--time-limit", "3", "--skip-events", *args, stdout=out)
        return out.getvalue()

    def test_demo_is_complete_and_respects_constraints(self):
        self.seed()
        main = GenerationRun.objects.get(dpo_program__isnull=True)
        dpo = GenerationRun.objects.get(dpo_program__isnull=False)
        # Полнота: все пары основной нагрузки и программы размещены.
        self.assertEqual((main.status, main.scheduled_classes.count()), ("SOLVED", 13))
        self.assertEqual((dpo.status, dpo.scheduled_classes.count()), ("SOLVED", 2))
        # Ограничения: пары ДПО не пересекаются с основными парами слушателей и аудиториями.
        dpo_slots = set(dpo.scheduled_classes.values_list("slot_id", flat=True))
        for membership in DPOMembership.objects.select_related("student"):
            main_slots = set(ScheduledClass.objects.filter(run=main)
                             .filter(student_holder_filter(membership.student)).values_list("slot_id", flat=True))
            self.assertFalse(main_slots & dpo_slots)
        main_rooms = set(main.scheduled_classes.filter(room__isnull=False).values_list("slot_id", "room_id"))
        dpo_rooms = set(dpo.scheduled_classes.filter(room__isnull=False).values_list("slot_id", "room_id"))
        self.assertFalse(main_rooms & dpo_rooms)
        # Демонстрация недельного лимита.
        self.assertTrue(GenerationIssue.objects.filter(run=main, text__contains="при лимите 3").exists())

    def test_repeat_requires_reset_and_reset_keeps_real_data(self):
        real = Teacher.objects.create(full_name="Настоящий преподаватель")
        self.seed()
        with self.assertRaisesMessage(CommandError, "--reset"):
            self.seed()
        output = self.seed("--reset")
        self.assertIn("Удаляю прежнее демо", output)
        self.assertEqual(Semester.objects.count(), 1)
        self.assertEqual(Teacher.objects.filter(full_name__endswith="(демо)").count(), 6)
        self.assertEqual(Location.objects.filter(name__startswith="Демо").count(), 1)
        self.assertEqual(GenerationRun.objects.count(), 2)
        self.assertTrue(Teacher.objects.filter(pk=real.pk).exists())
