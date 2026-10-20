"""Published schedules retain their source periods independently of solver results."""

from django.db import models


class ScheduleImport(models.Model):
    file_sha256 = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    source = models.JSONField(default=dict)
    source_metadata = models.JSONField(default=dict)
    group_count = models.PositiveIntegerField(default=0)
    lesson_count = models.PositiveIntegerField(default=0)
    review_required = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Импорт расписания"
        verbose_name_plural = "Импорты расписания"

    def __str__(self):
        return f"Import #{self.pk} ({self.source_metadata.get('date', '?')})"


class ImportedGroupSchedule(models.Model):
    schedule_import = models.ForeignKey(ScheduleImport, on_delete=models.CASCADE, related_name="groups")
    name = models.CharField(max_length=100)
    metadata = models.JSONField(default=dict)
    source = models.JSONField(default=dict)
    warnings = models.JSONField(default=list)
    review_required = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["schedule_import", "name"], name="uniq_import_group")]
        verbose_name = "Импортированная группа"
        verbose_name_plural = "Импортированные группы"

    def __str__(self):
        return f"{self.name} (import #{self.schedule_import_id})"


class ImportedLesson(models.Model):
    group = models.ForeignKey(ImportedGroupSchedule, on_delete=models.CASCADE, related_name="lessons")
    source_path = models.CharField(max_length=200)
    day_of_week = models.PositiveSmallIntegerField(choices=[
        (1, "Понедельник"), (2, "Вторник"), (3, "Среда"), (4, "Четверг"),
        (5, "Пятница"), (6, "Суббота"), (7, "Воскресенье"),
    ])
    lesson_number = models.PositiveSmallIntegerField()
    subject = models.TextField()
    lesson_type = models.TextField(blank=True)
    teacher_raw = models.TextField(blank=True)
    teachers = models.ManyToManyField("schedule_generator.Teacher", related_name="imported_lessons", blank=True)
    teacher_warnings = models.JSONField(default=list, blank=True)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    start = models.TimeField(null=True, blank=True)
    end = models.TimeField(null=True, blank=True)
    week_raw = models.TextField(blank=True)
    data = models.JSONField(help_text="Полная запись парсера: исходник, места, ссылки и предупреждения")

    class Meta:
        ordering = ["group__name", "day_of_week", "lesson_number", "id"]
        constraints = [models.UniqueConstraint(fields=["group", "source_path"], name="uniq_import_group_source_path")]
        verbose_name = "Импортированное занятие"
        verbose_name_plural = "Импортированные занятия"

    def __str__(self):
        return f"{self.subject} ({self.group.name}, {self.day_of_week}/{self.lesson_number})"
