"""Календарь семестра: семестр -> шаблон недели -> слоты (пары).

Иерархия:
    Semester (например «2026/27 осень», 18 недель)
      └── WeekPattern — набор пар в каждый день; чётная/нечётная недели
          └── TimeSlot — конкретная пара: день недели + порядковый номер.

Генератор строит НЕДЕЛЬНЫЙ ШАБЛОН: расписание размещается по слотам одного
WeekPattern и повторяется из недели в неделю.
"""

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class Semester(models.Model):
    name = models.CharField("Название", max_length=50, unique=True)  # например «2026/27 осень»
    start_date = models.DateField("Начало")
    weeks_count = models.PositiveSmallIntegerField("Недель", default=18)

    class Meta:
        verbose_name = "Семестр"
        verbose_name_plural = "Семестры"

    def __str__(self):
        return self.name


class WeekPattern(models.Model):
    """Шаблон недели: набор пар (слотов) в каждый день.

    Один семестр может иметь чётную/нечётную недели — тогда паттернов два.
    """

    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name="week_patterns")
    name = models.CharField("Название", max_length=50, default="Базовая неделя")
    parity = models.PositiveSmallIntegerField(
        "Чётность недели",
        choices=[(0, "Любая"), (1, "Нечётная"), (2, "Чётная")],
        default=0,
    )
    bell_schedule = models.JSONField("Источник сетки звонков", default=dict, blank=True)

    class Meta:
        verbose_name = "Шаблон недели"
        verbose_name_plural = "Шаблоны недель"
        unique_together = [("semester", "name")]

    def __str__(self):
        return f"{self.semester.name}: {self.name}"


class TimeSlot(models.Model):
    """Пара в сетке расписания: день недели + порядковый номер пары."""

    DAY_CHOICES = [
        (0, "Понедельник"), (1, "Вторник"), (2, "Среда"),
        (3, "Четверг"), (4, "Пятница"), (5, "Суббота"), (6, "Воскресенье"),
    ]

    week_pattern = models.ForeignKey(WeekPattern, on_delete=models.CASCADE, related_name="slots")
    day_of_week = models.PositiveSmallIntegerField("День недели", choices=DAY_CHOICES)
    lesson_number = models.PositiveSmallIntegerField("Номер пары")
    start_time = models.TimeField("Начало", null=True, blank=True)
    end_time = models.TimeField("Конец", null=True, blank=True)

    class Meta:
        verbose_name = "Слот (пара)"
        verbose_name_plural = "Слоты (пары)"
        unique_together = [("week_pattern", "day_of_week", "lesson_number")]
        ordering = ["day_of_week", "lesson_number"]

    def __str__(self):
        return f"{self.get_day_of_week_display()} {self.lesson_number}-я пара"

    def save(self, *args, **kwargs):
        self.validate_history()
        return super().save(*args, **kwargs)

    def validate_history(self):
        if self.pk:
            fields = ("week_pattern_id", "day_of_week", "lesson_number", "start_time", "end_time")
            previous = type(self).objects.filter(pk=self.pk).values(*fields).first()
            if previous and any(previous[field] != getattr(self, field) for field in fields) \
                    and self.classes.exists():
                raise ValidationError("Слот сохранённого расписания менять нельзя: создайте новый шаблон.")

    def clean(self):
        super().clean()
        self.validate_history()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "Конец должен быть позже начала."})


class Module(models.Model):
    """Календарный модуль. Не является номером семестра в учебном плане."""
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name="modules")
    number = models.PositiveSmallIntegerField("Номер", validators=[MinValueValidator(1)])
    start_date = models.DateField("Начало")
    end_date = models.DateField("Конец")
    teaching_weeks = models.PositiveSmallIntegerField("Учебных недель", validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "Модуль"
        verbose_name_plural = "Модули"
        constraints = [
            models.UniqueConstraint(fields=["semester", "number"], name="uniq_semester_module"),
            models.CheckConstraint(condition=models.Q(end_date__gte=models.F("start_date")), name="module_dates_order"),
            models.CheckConstraint(condition=models.Q(teaching_weeks__gte=1), name="module_positive_weeks"),
        ]

    def __str__(self):
        return f"{self.semester}: модуль {self.number}"

    def clean(self):
        super().clean()
        if self.semester_id and self.start_date and self.start_date < self.semester.start_date:
            raise ValidationError({"start_date": "Модуль начинается раньше семестра."})
