"""Уже назначенные занятия ПД: генератор размещает остальные пары вокруг них."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models


class FixedProjectSession(models.Model):
    project = models.ForeignKey("schedule_generator.Project", on_delete=models.CASCADE,
                               related_name="fixed_sessions", verbose_name="Проект")
    module = models.ForeignKey("schedule_generator.Module", on_delete=models.PROTECT,
                              null=True, blank=True, related_name="fixed_pd_sessions")
    teacher = models.ForeignKey("schedule_generator.Teacher", on_delete=models.PROTECT,
                               null=True, blank=True, related_name="fixed_pd_sessions")
    day_of_week = models.PositiveSmallIntegerField("День недели", validators=[MaxValueValidator(6)])
    start_time = models.TimeField("Начало")
    end_time = models.TimeField("Конец")
    date = models.DateField("Дата (пусто — еженедельно)", null=True, blank=True)
    room = models.ForeignKey("schedule_generator.Room", on_delete=models.PROTECT,
                             null=True, blank=True, related_name="fixed_pd_sessions")
    place_details = models.JSONField("Место из источника", default=dict)
    travel_buffer_minutes = models.PositiveSmallIntegerField(
        "Запас на дорогу", validators=[MaxValueValidator(240)])
    source_key = models.CharField("Ключ записи импорта", max_length=150)
    source = models.JSONField("Источник и координаты ячейки", default=dict, blank=True)

    class Meta:
        verbose_name = "Фиксированное занятие ПД"
        verbose_name_plural = "Фиксированные занятия ПД"
        constraints = [
            models.UniqueConstraint(fields=["project", "source_key"], name="uniq_project_fixed_source"),
            models.CheckConstraint(condition=models.Q(end_time__gt=models.F("start_time")),
                                   name="fixed_pd_positive_time"),
            models.CheckConstraint(condition=models.Q(day_of_week__lte=6), name="fixed_pd_valid_day"),
        ]

    def __str__(self):
        return f"{self.project}: {self.date or self.day_of_week} {self.start_time}–{self.end_time}"

    def clean(self):
        super().clean()
        errors = {}
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            errors["end_time"] = "Конец должен быть позже начала в тот же день."
        if self.project_id:
            semester = self.project.semester
            if self.module_id and self.module.semester_id != semester.pk:
                errors["module"] = "Модуль относится к другому семестру."
            if self.date:
                end = semester.start_date + timedelta(weeks=semester.weeks_count)
                if not semester.start_date <= self.date < end:
                    errors["date"] = "Дата вне семестра."
                if self.module_id and not self.module.start_date <= self.date <= self.module.end_date:
                    errors["date"] = "Дата вне выбранного модуля."
                if self.date.weekday() != self.day_of_week:
                    errors["day_of_week"] = "День недели не соответствует дате."
        additional = (self.place_details.get("venue") or {}).get("kind") == "additional"
        if self.room_id and (additional or self.place_details.get("resource_policy", {}).get("reserve_room") is False):
            errors["room"] = "Дополнительная площадка не резервирует аудиторию Политеха."
        if errors:
            raise ValidationError(errors)
