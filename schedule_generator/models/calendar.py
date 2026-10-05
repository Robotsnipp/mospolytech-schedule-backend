"""Календарь семестра: семестр -> шаблон недели -> слоты (пары).

Иерархия:
    Semester (например «2026/27 осень», 18 недель)
      └── WeekPattern — набор пар в каждый день; чётная/нечётная недели
          └── TimeSlot — конкретная пара: день недели + порядковый номер.

Генератор строит НЕДЕЛЬНЫЙ ШАБЛОН: расписание размещается по слотам одного
WeekPattern и повторяется из недели в неделю.
"""

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
        (3, "Четверг"), (4, "Пятница"), (5, "Суббота"),
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
