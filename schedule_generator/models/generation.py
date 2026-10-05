"""Журнал генерации: запуски решателя и найденные проблемы."""

from django.db import models


class GenerationRun(models.Model):
    """Один запуск генерации расписания (история сохраняется)."""

    STATUS_CHOICES = [
        ("PENDING", "Ожидание"),
        ("SOLVED", "Решено"),
        ("PARTIAL", "Частично"),
        ("INFEASIBLE", "Неразрешимо"),
        ("ERROR", "Ошибка"),
    ]

    semester = models.ForeignKey("schedule_generator.Semester", on_delete=models.CASCADE,
                                 related_name="runs", verbose_name="Семестр")
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    status = models.CharField("Статус", max_length=20, choices=STATUS_CHOICES, default="PENDING")
    objective_value = models.IntegerField("Значение целевой функции", null=True, blank=True)
    solve_time_seconds = models.FloatField("Время решения (сек)", null=True, blank=True)
    message = models.TextField("Сообщение", blank=True)

    class Meta:
        verbose_name = "Запуск генерации"
        verbose_name_plural = "Запуски генерации"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Run #{self.pk} ({self.semester}, {self.status})"


class GenerationIssue(models.Model):
    """Проблема/предупреждение процесса генерации (например, unscheduled event)."""

    run = models.ForeignKey(GenerationRun, on_delete=models.CASCADE, related_name="issues")
    event = models.ForeignKey("schedule_generator.LessonEvent", on_delete=models.SET_NULL,
                              null=True, blank=True)
    level = models.CharField("Уровень", max_length=10, choices=[
        ("WARNING", "Предупреждение"), ("ERROR", "Ошибка"),
    ], default="WARNING")
    text = models.TextField("Текст")

    class Meta:
        verbose_name = "Проблема генерации"
        verbose_name_plural = "Проблемы генерации"

    def __str__(self):
        return f"[{self.level}] {self.text[:80]}"
