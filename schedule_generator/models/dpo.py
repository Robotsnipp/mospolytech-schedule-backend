"""Программы ДПО: отдельный набор групп со своими дисциплинами.

    DPOProgram     — программа (активность) в семестре/модуле: её группы ДПО,
                     число учебных недель и основной запуск, с которым надо
                     считаться при планировании.
    DPOMembership  — студент основного обучения входит в группу ДПО
                     (у студента остаётся своя учебная группа).
    DPODiscipline  — дисциплина программы с общим объёмом в часах или парах.

Подготовка нагрузки и планирование — services/dpo.py.
"""

from django.core.exceptions import ValidationError
from django.db import models

from ..enums import Format

ACADEMIC_HOURS_PER_PAIR = 2


class DPOProgram(models.Model):
    name = models.CharField("Название", max_length=200)
    semester = models.ForeignKey("schedule_generator.Semester", on_delete=models.CASCADE,
                                 related_name="dpo_programs", verbose_name="Семестр")
    module = models.ForeignKey("schedule_generator.Module", on_delete=models.PROTECT,
                               null=True, blank=True, related_name="dpo_programs",
                               verbose_name="Модуль")
    weeks = models.PositiveSmallIntegerField(
        "Учебных недель", null=True, blank=True,
        help_text="Если пусто, берётся число учебных недель модуля.")
    groups = models.ManyToManyField(
        "schedule_generator.Group", blank=True, related_name="dpo_programs",
        limit_choices_to={"is_dpo": True}, verbose_name="Группы ДПО")
    base_run = models.ForeignKey(
        "schedule_generator.GenerationRun", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="Основное расписание",
        help_text="Запуск генерации основного обучения. Его пары считаются занятостью "
                  "студентов, преподавателей и аудиторий. Если пусто, берётся последний "
                  "успешный запуск того же семестра и модуля.")
    description = models.TextField("Описание", blank=True)

    class Meta:
        verbose_name = "Программа ДПО"
        verbose_name_plural = "Программы ДПО"
        ordering = ["name", "id"]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.module_id and self.module.semester_id != self.semester_id:
            raise ValidationError({"module": "Модуль относится к другому семестру."})
        if self.base_run_id and self.base_run.semester_id != self.semester_id:
            raise ValidationError({"base_run": "Запуск относится к другому семестру."})

    @property
    def effective_weeks(self) -> int | None:
        if self.weeks:
            return self.weeks
        return self.module.teaching_weeks if self.module_id else None


class DPOMembership(models.Model):
    group = models.ForeignKey("schedule_generator.Group", on_delete=models.CASCADE,
                              related_name="dpo_memberships", limit_choices_to={"is_dpo": True},
                              verbose_name="Группа ДПО")
    student = models.ForeignKey("schedule_generator.Student", on_delete=models.CASCADE,
                                related_name="dpo_memberships", verbose_name="Студент")

    class Meta:
        verbose_name = "Слушатель ДПО"
        verbose_name_plural = "Слушатели ДПО"
        constraints = [models.UniqueConstraint(fields=["group", "student"],
                                               name="uniq_dpo_membership")]

    def __str__(self):
        return f"{self.student} → {self.group}"

    def clean(self):
        super().clean()
        if self.group_id and not self.group.is_dpo:
            raise ValidationError({"group": "Слушателя можно добавить только в группу ДПО."})
        if self.student_id and self.student.group.is_dpo:
            raise ValidationError({"student": "Нужен студент основного обучения."})


class DPODiscipline(models.Model):
    program = models.ForeignKey(DPOProgram, on_delete=models.CASCADE,
                                related_name="disciplines", verbose_name="Программа")
    subject = models.ForeignKey("schedule_generator.Subject", on_delete=models.PROTECT,
                                related_name="+", verbose_name="Дисциплина")
    teacher = models.ForeignKey("schedule_generator.Teacher", on_delete=models.PROTECT,
                                related_name="+", verbose_name="Преподаватель")
    format = models.CharField("Формат", max_length=10, choices=Format.choices,
                              default=Format.ONSITE)
    total_hours = models.PositiveIntegerField(
        "Всего академических часов", null=True, blank=True,
        help_text="2 академических часа = 1 пара. Укажите часы или пары.")
    total_pairs = models.PositiveIntegerField("Всего пар", null=True, blank=True)

    class Meta:
        verbose_name = "Дисциплина ДПО"
        verbose_name_plural = "Дисциплины ДПО"
        constraints = [models.UniqueConstraint(fields=["program", "subject"],
                                               name="uniq_dpo_discipline")]

    def __str__(self):
        return f"{self.program}: {self.subject}"

    def clean(self):
        super().clean()
        if (self.total_hours is None) == (self.total_pairs is None):
            raise ValidationError("Укажите либо всего часов, либо всего пар.")
        if self.total_hours is not None and self.total_hours % ACADEMIC_HOURS_PER_PAIR:
            raise ValidationError({"total_hours": "Часы должны делиться на 2 (одна пара - 2 часа)."})

    @property
    def pairs_total(self) -> int:
        if self.total_pairs is not None:
            return self.total_pairs
        return (self.total_hours or 0) // ACADEMIC_HOURS_PER_PAIR
