"""Immutable manual alternatives to a genuine solver result."""

from django.core.exceptions import ValidationError
from django.db import models


class ManualScheduleVariant(models.Model):
    source_run = models.ForeignKey(
        "schedule_generator.GenerationRun", on_delete=models.PROTECT,
        related_name="manual_variants")
    parent_variant = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
        related_name="subsequent_variants")
    name = models.CharField(max_length=120, default="Ручной вариант")
    created_at = models.DateTimeField(auto_now_add=True)
    validation = models.JSONField(default=dict)
    calendar_snapshot = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Сохранённый вариант неизменяем: создайте новый вариант.")
        return super().save(*args, **kwargs)


class ManualPlacement(models.Model):
    variant = models.ForeignKey(
        ManualScheduleVariant, on_delete=models.CASCADE, related_name="placements")
    source_class = models.ForeignKey(
        "schedule_generator.ScheduledClass", on_delete=models.PROTECT,
        related_name="manual_placements")
    event_id = models.PositiveIntegerField()
    slot_id = models.PositiveIntegerField()
    room_id = models.PositiveIntegerField(null=True, blank=True)
    calendar_row = models.JSONField(default=dict)

    class Meta:
        ordering = ["source_class_id"]
        constraints = [models.UniqueConstraint(
            fields=["variant", "source_class"], name="unique_manual_variant_source_class")]

    def __str__(self):
        return f"{self.variant_id}: {self.source_class_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Сохранённое размещение неизменяемо.")
        return super().save(*args, **kwargs)
