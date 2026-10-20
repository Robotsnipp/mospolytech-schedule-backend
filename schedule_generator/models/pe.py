"""Физкультура: направления, слоты и запись студентов.

PE не участвует в генерации (см. enums.LessonType.PE): студент сам выбирает
слоты внутри направления. Ограничения выбора проверяет API
(services/pe.py), здесь — только данные и уникальность.
"""

from django.db import models


class PEDirection(models.Model):
    code = models.SlugField("Код", max_length=40, unique=True)
    name = models.CharField("Название", max_length=100)
    icon = models.CharField("Иконка", max_length=8, blank=True)

    class Meta:
        verbose_name = "Направление физкультуры"
        verbose_name_plural = "Направления физкультуры"

    def __str__(self):
        return self.name


class PESlot(models.Model):
    direction = models.ForeignKey(PEDirection, on_delete=models.CASCADE,
                                  related_name="slots", verbose_name="Направление")
    day_of_week = models.PositiveSmallIntegerField("День недели (0 — пн … 5 — сб)")
    start_time = models.TimeField("Начало")
    end_time = models.TimeField("Конец")
    place = models.CharField("Место", max_length=100, blank=True)

    class Meta:
        verbose_name = "Слот физкультуры"
        verbose_name_plural = "Слоты физкультуры"
        constraints = [
            models.UniqueConstraint(fields=["direction", "day_of_week", "start_time"],
                                    name="uniq_pe_slot"),
        ]

    def __str__(self):
        return f"{self.direction} {self.day_of_week} {self.start_time:%H:%M}"


class PEEnrollment(models.Model):
    """Запись на слот. Владелец — идентификатор браузера (входа в системе нет)."""

    owner = models.CharField("Владелец (client id)", max_length=64, default="")
    group_title = models.CharField("Группа", max_length=50, blank=True, default="")
    slot = models.ForeignKey(PESlot, on_delete=models.CASCADE,
                             related_name="enrollments", verbose_name="Слот")

    class Meta:
        verbose_name = "Запись на физкультуру"
        verbose_name_plural = "Записи на физкультуру"
        constraints = [
            models.UniqueConstraint(fields=["owner", "slot"], name="uniq_pe_enrollment_owner"),
        ]

    def __str__(self):
        return f"{self.owner} -> {self.slot_id}"
