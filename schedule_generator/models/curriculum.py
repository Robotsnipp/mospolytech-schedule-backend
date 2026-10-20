"""Разобранные учебные планы; часы относятся к семестру обучения, не календарю."""
from django.core.validators import MinValueValidator
from django.db import models


class StudyForm(models.TextChoices):
    FULL_TIME = "FULL_TIME", "Очная"
    PART_TIME = "PART_TIME", "Очно-заочная"
    EXTRAMURAL = "EXTRAMURAL", "Заочная"


class Curriculum(models.Model):
    name = models.CharField("Название", max_length=200)
    program_code = models.CharField("Код направления", max_length=20)
    profile = models.CharField("Профиль", max_length=200)
    admission_year = models.PositiveSmallIntegerField("Год набора", validators=[MinValueValidator(1)])
    study_form = models.CharField("Форма обучения", max_length=20, choices=StudyForm.choices)
    duration_months = models.PositiveSmallIntegerField("Срок обучения, месяцев", validators=[MinValueValidator(1)])
    version = models.CharField("Версия", max_length=50, default="1")
    source_file = models.FileField("Исходный план", upload_to="curricula/", blank=True)
    source = models.JSONField("Происхождение импорта", default=dict, blank=True)

    class Meta:
        verbose_name = "Учебный план"
        verbose_name_plural = "Учебные планы"
        constraints = [models.UniqueConstraint(fields=["program_code", "profile", "admission_year", "study_form", "version"], name="uniq_curriculum_version")]

    def __str__(self):
        return f"{self.name} ({self.admission_year}, {self.get_study_form_display()}, {self.version})"


class CurriculumEntry(models.Model):
    curriculum = models.ForeignKey(Curriculum, on_delete=models.CASCADE, related_name="entries")
    subject = models.ForeignKey("schedule_generator.Subject", on_delete=models.PROTECT, related_name="curriculum_entries")
    discipline_code = models.CharField("Код строки плана", max_length=50)
    semester_number = models.PositiveSmallIntegerField("Семестр обучения", validators=[MinValueValidator(1)])
    contact_hours = models.PositiveIntegerField("Аудиторные часы семестра", null=True, blank=True)
    lecture_hours = models.PositiveIntegerField("Часы лекций", default=0, null=True, blank=True)
    practice_hours = models.PositiveIntegerField("Часы семинаров", default=0, null=True, blank=True)
    lab_hours = models.PositiveIntegerField("Часы лабораторных", default=0, null=True, blank=True)
    independent_hours = models.PositiveIntegerField("Самостоятельная работа", default=0, null=True, blank=True)
    has_exam = models.BooleanField("Экзамен", default=False, null=True, blank=True)
    has_credit = models.BooleanField("Зачёт", default=False, null=True, blank=True)
    has_graded_credit = models.BooleanField("Дифференцированный зачёт", default=False, null=True, blank=True)
    source = models.JSONField("Строка источника и проверка", default=dict, blank=True)

    class Meta:
        verbose_name = "Семестровая нагрузка плана"
        verbose_name_plural = "Семестровые нагрузки плана"
        constraints = [models.UniqueConstraint(fields=["curriculum", "discipline_code", "semester_number"], name="uniq_curriculum_entry")]

    def __str__(self):
        return f"{self.subject}: семестр {self.semester_number}"
