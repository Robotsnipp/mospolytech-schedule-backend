"""Участники учебного процесса: группы, студенты, преподаватели."""

import re

from django.core.exceptions import ValidationError
from django.db import models

from ..enums import Chronotype

# Формат названия учебной группы: год зачисления (2 цифры) + дефис + номер группы.
# Пример: «241-334» — 24 — год зачисления, 1 — дополнительный признак,
# 334 — номер группы. Всего в названии не более 6 цифр.
GROUP_NAME_RE = re.compile(r"^\d{3,6}-\d{1,5}$")

# Формат названия группы ДПО: префикс «ДПО», далее произвольная строка
# (обычно код программы/потока), например «ДПО-2026-07» или «ДПО Python».
DPO_GROUP_NAME_RE = re.compile(r"^ДПО[-— ]?[\wА-Яа-яЁё\-— ./-]{1,40}$")


def admission_year_from_group_name(name: str) -> int | None:
    """Год зачисления из названия группы: первые две цифры («241-334» → 2024)."""
    match = re.match(r"^(\d{2})", name or "")
    if not match:
        return None
    return 2000 + int(match.group(1))


class Group(models.Model):
    """Учебная группа (для очных пар и лекционных потоков).

    Название по формату университета: «241-334», где первые две цифры —
    год зачисления (2024), остальные — код/номер группы.

    Отдельный вид групп — группы ДПО (дополнительное профессиональное
    образование): для каждой программы ДПО создаётся своя группа со
    слушателями; пары ДПО планируются на такую группу как на обычного
    носителя занятия (см. LessonType.DPO). Для групп ДПО действует свой
    формат названия: префикс «ДПО» + произвольная метка программы,
    например «ДПО-2026-07». Год зачисления у них не вычисляется.
    """

    name = models.CharField(
        "Название", max_length=50, unique=True,
        help_text="Учебная группа: «241-334» (первые две цифры — год зачисления). "
                  "Группа ДПО: «ДПО-<программа>», например «ДПО-2026-07».",
    )
    admission_year = models.PositiveSmallIntegerField(
        "Год зачисления",
        null=True, blank=True,
        help_text="Если пусто, вычисляется автоматически из названия "
                  "(первые две цифры: «241-334» → 2024). "
                  "Для групп ДПО не заполняется.",
    )
    semester = models.ForeignKey(
        "schedule_generator.Semester", on_delete=models.CASCADE, related_name="groups",
        verbose_name="Семестр", null=True, blank=True,
    )
    curriculum = models.ForeignKey("schedule_generator.Curriculum", on_delete=models.PROTECT, null=True, blank=True, related_name="groups", verbose_name="Учебный план")
    is_first_year_first_semester = models.BooleanField(
        "Перваки, первый семестр",
        default=False,
        help_text="Такие группы не участвуют в проектной деятельности.",
    )
    is_dpo = models.BooleanField(
        "Группа ДПО",
        default=False,
        help_text="Группа дополнительного профессионального образования: "
                  "создаётся отдельно для каждого ДПО.",
    )

    class Meta:
        verbose_name = "Группа"
        verbose_name_plural = "Группы"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.is_dpo and self.admission_year is None and self.name:
            self.admission_year = admission_year_from_group_name(self.name)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors = {}
        if self.name and self.is_dpo:
            # Для групп ДПО — свой формат: префикс «ДПО» + метка программы.
            if not DPO_GROUP_NAME_RE.match(self.name):
                errors["name"] = (
                    "Неверный формат названия группы ДПО. Ожидается вид "
                    "«ДПО-<программа>», например «ДПО-2026-07»."
                )
        elif self.name and not GROUP_NAME_RE.match(self.name):
            errors["name"] = (
                "Неверный формат названия группы. Ожидается вид «241-334»: "
                "первые две цифры — год зачисления, всего не более 6 цифр."
            )
        # Год зачисления вычисляем из названия только для обычных групп.
        if not self.is_dpo and self.admission_year is None and self.name:
            derived = admission_year_from_group_name(self.name)
            if derived is not None:
                self.admission_year = derived
        if errors:
            raise ValidationError(errors)    


class Student(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="students",
                              verbose_name="Учебная группа")
    full_name = models.CharField("ФИО", max_length=150)

    class Meta:
        verbose_name = "Студент"
        verbose_name_plural = "Студенты"

    def __str__(self):
        return self.full_name


class Teacher(models.Model):
    """Преподаватель. Лимиты пар используются генератором
    (жёстко: одна пара в слот; мягко: перегрузка по max_pairs_per_day)."""

    full_name = models.CharField("ФИО", max_length=150)
    max_pairs_per_day = models.PositiveSmallIntegerField("Макс. пар в день", default=4)
    max_pairs_per_week = models.PositiveSmallIntegerField("Макс. пар в неделю", default=20)
    chronotype = models.CharField(
        "Хронотип", max_length=10, choices=Chronotype.choices, default=Chronotype.NEUTRAL,
        help_text="Мягкое пожелание: жаворонку не ставят поздние пары, "
                  "сове — ранние. Не отменяет размещения нагрузки.",
    )

    class Meta:
        verbose_name = "Преподаватель"
        verbose_name_plural = "Преподаватели"

    def __str__(self):
        return self.full_name
