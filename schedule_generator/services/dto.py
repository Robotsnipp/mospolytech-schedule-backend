"""Входные данные модели CP-SAT и результаты генерации.

Словарь типов:
- ``EventSpec``      — подготовленные данные одного LessonEvent для модели
                      (что блокирует, сколько пар нужно, очное/онлайн);
- ``GenerationResult`` — плоский результат работы решателя (без ORM);
- ``RunOutcome``     — итог полного цикла генерации (run + сводка).

``EventSpec` — граница между ORM-миром (models/) и чистой математикой
(services/generator.py): генератор не знает про Django, только про specs.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EventSpec:
    """Подготовленные данные одного события для модели."""

    event_id: int
    lesson_type: str
    format: str                   # ONSITE | ONLINE
    pairs_per_week: int
    audience_size: int
    teacher_id: int | None
    # Учебные группы, целиком блокируемые занятием в слоте
    # (практика/лаборатория на группу, все группы-слушатели лекции).
    blocking_group_ids: set[int] = field(default_factory=set)
    # Отдельные студенты, блокируемые занятием (ПД: только выбравшие проект;
    # внеучебка на студентов — при необходимости).
    blocking_student_ids: set[int] = field(default_factory=set)
    # Для лекций: id потока — все его группы обязаны слушать в одном слоте
    stream_id: int | None = None


@dataclass
class GenerationResult:
    """Плоский ответ решателя: что куда поставлено, что не удалось."""

    status: str
    objective_value: int | None
    solve_time: float
    assignments: list[dict]        # {event_id, slot_id, room_id|None}
    unscheduled_event_ids: list[int]
    message: str = ""


@dataclass
class RunOutcome:
    """Итог полного цикла генерации после записи в БД (сервис generation)."""

    run_id: int
    status: str
    scheduled_count: int
    issue_count: int
