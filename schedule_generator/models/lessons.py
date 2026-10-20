"""Планирование занятий: LessonEvent (план) -> ScheduledClass (результат).

Поток данных фичи:

    LessonEvent  — «что нужно запланировать»: занятие для носителя
                   (учебная группа / лекционный поток / проект ПД) с требуемым
                   числом пар в неделю и типом.
                       │  генератор (services/generator.py) размещает пары
                       ▼
    ScheduledClass — «результат генерации»: конкретная пара в слот и аудиторию,
                    привязанная к запуску (GenerationRun).
"""

from django.core.exceptions import ValidationError
from django.db import models

from ..enums import DEFAULT_FORMAT_BY_TYPE, Format, LessonType


class LessonEvent(models.Model):
    """Единица планирования: сколько пар какого типа нужно провести.

    Носитель занятия — одно из:
      - group   (практика/лаборатория/внеучебка/ДПО на учебную группу
                 или группу ДПО),
      - stream  (лекция на поток групп),
      - project (ПД на выбранную группу студентов).
    Физкультура (PE) тоже может храниться здесь, но генератор её пропускает.
    """

    semester = models.ForeignKey("schedule_generator.Semester", on_delete=models.CASCADE,
                                 related_name="events", verbose_name="Семестр")
    subject = models.ForeignKey("schedule_generator.Subject", on_delete=models.CASCADE,
                                related_name="events", verbose_name="Дисциплина")
    lesson_type = models.CharField("Тип занятия", max_length=20, choices=LessonType.choices)
    # Legacy NULL values remain supported; normalizing them needs a data migration.
    format = models.CharField(  # noqa: DJ001
        "Формат", max_length=10, choices=Format.choices, null=True, blank=True,
        help_text="Заполняется только для внеучебки и ДПО; "
                  "остальные типы имеют фиксированный формат.",
    )
    group = models.ForeignKey("schedule_generator.Group", on_delete=models.CASCADE, null=True, blank=True,
                              related_name="events", verbose_name="Группа")
    stream = models.ForeignKey("schedule_generator.LectureStream", on_delete=models.CASCADE,
                               null=True, blank=True,
                               related_name="events", verbose_name="Лекционный поток")
    project = models.ForeignKey("schedule_generator.Project", on_delete=models.CASCADE,
                                null=True, blank=True,
                                related_name="events", verbose_name="Проект (ПД)")
    teacher = models.ForeignKey("schedule_generator.Teacher", on_delete=models.SET_NULL,
                                null=True, blank=True,
                                related_name="events", verbose_name="Преподаватель")
    module = models.ForeignKey("schedule_generator.Module", on_delete=models.PROTECT, null=True, blank=True, related_name="events")
    curriculum_entry = models.ForeignKey("schedule_generator.CurriculumEntry", on_delete=models.PROTECT, null=True, blank=True, related_name="events")
    pairs_per_week = models.PositiveSmallIntegerField("Пар в неделю", default=1)
    audience_size = models.PositiveSmallIntegerField("Слушателей", default=25)
    academic_hours = models.PositiveIntegerField("Академических часов в модуле", null=True, blank=True)
    prepared_for_group = models.ForeignKey(
        "schedule_generator.Group", on_delete=models.PROTECT, null=True, blank=True,
        related_name="prepared_events", verbose_name="Группа подготовки нагрузки")
    preparation_source = models.JSONField("Основание подготовки нагрузки", default=dict, blank=True)

    class Meta:
        verbose_name = "Занятие (план)"
        verbose_name_plural = "Занятия (план)"
        constraints = [models.UniqueConstraint(
            fields=["prepared_for_group", "curriculum_entry", "module", "lesson_type"],
            condition=models.Q(prepared_for_group__isnull=False), name="uniq_prepared_module_load")]

    def __str__(self):
        holder = self.group or self.stream or self.project
        return f"{self.subject} [{self.get_lesson_type_display()}] — {holder}"

    def clean(self):
        super().clean()
        errors = {}
        if self.module_id and self.module.semester_id != self.semester_id:
            errors["module"] = "Модуль относится к другому семестру."
        if self.curriculum_entry_id:
            if self.curriculum_entry.subject_id != self.subject_id:
                errors["curriculum_entry"] = "Предмет не совпадает со строкой плана."
            if self.group_id and self.group.curriculum_id != self.curriculum_entry.curriculum_id:
                errors["curriculum_entry"] = "Строка относится к другому учебному плану группы."
        holders = [self.group_id, self.stream_id, self.project_id]
        if sum(1 for h in holders if h) != 1:
            errors["holder"] = (
                "У занятия должен быть ровно один носитель: группа, "
                "лекционный поток или проект (ПД)."
            )
        if self.lesson_type == LessonType.LECTURE and not self.stream_id:
            errors["stream"] = "Лекция должна быть привязана к лекционному потоку."
        if self.lesson_type == LessonType.PD and not self.project_id:
            errors["project"] = "Пара ПД должна быть привязана к проекту."
        if self.lesson_type in (LessonType.PRACTICE, LessonType.LAB) and not self.group_id:
            errors["group"] = "Практика/лаборатория планируются на учебную группу."
        # ДПО: пары планируются на отдельную группу ДПО; формат — очный или
        # онлайн — задаётся данными события (как у внеучебки).
        if self.lesson_type == LessonType.DPO:
            if not self.group_id:
                errors["group"] = "Пара ДПО должна быть привязана к группе ДПО."
            elif not self.group.is_dpo:
                errors["group"] = "Занятие ДПО должно относиться к группе ДПО."
        if self.group_id and self.group.is_dpo \
                and self.lesson_type not in (LessonType.DPO, LessonType.EXTRACURRICULAR):
            errors["group"] = (
                "На группу ДПО можно планировать только занятия ДПО"
                " или внеучебку."
            )
        # Формат у жёстких типов фиксирован — не даём задать противоречивое значение.
        if self.lesson_type in DEFAULT_FORMAT_BY_TYPE and self.format:
            expected = DEFAULT_FORMAT_BY_TYPE[self.lesson_type]
            if self.format != expected:
                errors["format"] = (
                    f"Тип «{self.get_lesson_type_display()}» всегда проходит в формате "
                    f"«{expected.label}»; поле формата можно задавать только для внеучебки."
                )
        if self.lesson_type not in DEFAULT_FORMAT_BY_TYPE and self.format == Format.ONLINE \
                and not self.group_id and not self.project_id:
            errors["format"] = "Онлайн-занятие должно быть привязано к группе."
        if self.pairs_per_week is not None and self.pairs_per_week < 0:
            errors["pairs_per_week"] = "Не может быть отрицательным."
        if errors:
            raise ValidationError(errors)


    @property
    def effective_format(self) -> str:
        from ..enums import effective_format as _ef
        return _ef(self.lesson_type, self.format)


class ScheduledClass(models.Model):
    """Результат генерации: конкретная пара в конкретный слот."""

    event = models.ForeignKey(LessonEvent, on_delete=models.CASCADE, related_name="classes",
                              verbose_name="Занятие")
    slot = models.ForeignKey("schedule_generator.TimeSlot", on_delete=models.CASCADE,
                             related_name="classes", verbose_name="Слот")
    room = models.ForeignKey("schedule_generator.Room", on_delete=models.SET_NULL,
                             null=True, blank=True,
                             related_name="classes", verbose_name="Аудитория")
    run = models.ForeignKey("schedule_generator.GenerationRun", on_delete=models.CASCADE,
                            related_name="scheduled_classes", verbose_name="Запуск")

    class Meta:
        verbose_name = "Пара в расписании"
        verbose_name_plural = "Пары в расписании"
        constraints = [
            models.UniqueConstraint(fields=["run", "event", "slot"], name="uniq_run_event_slot"),
        ]

    def __str__(self):
        return f"{self.event.subject} @ {self.slot}"
