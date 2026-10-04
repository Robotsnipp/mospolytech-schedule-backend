"""Перечисления для генератора расписания."""

from django.db import models


class LessonType(models.TextChoices):
    """Тип занятия.

    - PRACTICE / LAB — очные пары, планируются на одну группу.
    - LECTURE — лекции, проходят онлайн, планируются на несколько групп
      (объединяются в потоки).
    - PE — физкультура: НЕ участвует в генерации. Студент может прийти
      в любое время и в любое подходящее место.
    - PD — проектная деятельность: пары всегда очные; группа по ПД
      формируется из студентов, выбравших один и тот же проект
      (не зависит от учебной группы). Перваки на первом семестре ПД не выбирают.
    - EXTRACURRICULAR — внеучебка: пока мало информации, поэтому поддержка
      заложена с флагом is_online (может быть как очной, так и онлайн)
      и тоже участвует в генерации.
    """

    PRACTICE = ("PRACTICE", "Практика")
    LAB = ("LAB", "Лабораторная")
    LECTURE = ("LECTURE", "Лекция")
    PE = ("PE", "Физкультура")
    PD = ("PD", "Проектная деятельность")
    EXTRACURRICULAR = ("EXTRACURRICULAR", "Внеучебная деятельность")


class Format(models.TextChoices):
    """Формат проведения занятия."""

    ONSITE = ("ONSITE", "Очно")
    ONLINE = ("ONLINE", "Онлайн")


# Типы занятий, которые вообще не участвуют в генерации расписания.
NON_GENERATED_LESSON_TYPES = {LessonType.PE}

# Жёсткое соответствие «тип -> формат» для известных типов.
# Внеучебка может быть любым — выбирается данными (Format на Event).
DEFAULT_FORMAT_BY_TYPE = {
    LessonType.PRACTICE: Format.ONSITE,
    LessonType.LAB: Format.ONSITE,
    LessonType.LECTURE: Format.ONLINE,
    LessonType.PD: Format.ONSITE,
}


def effective_format(lesson_type: str, fmt: str | None) -> str:
    """Возвращает формат занятия: для жёстких типов — константу,
    иначе — формат из данных (для внеучебки), по умолчанию очный."""
    if lesson_type in DEFAULT_FORMAT_BY_TYPE:
        return DEFAULT_FORMAT_BY_TYPE[lesson_type]
    if fmt:
        return fmt
    return Format.ONSITE
