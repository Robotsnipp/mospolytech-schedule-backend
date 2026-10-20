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
from datetime import date


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
    # Группы отдельных участников ПД: конфликтуют с общей парой группы,
    # но не запрещают два разных проекта с непересекающимися студентами.
    participant_group_ids: set[int] = field(default_factory=set)


@dataclass(frozen=True)
class FixedPDSpec:
    session_id: int
    project_id: int
    day_of_week: int
    start_minute: int
    end_minute: int
    teacher_id: int
    student_ids: frozenset[int]
    participant_group_ids: frozenset[int]
    room_id: int | None
    place_raw: str
    travel_buffer_minutes: int
    occurrence_dates: frozenset[date]


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
    fixed_pd_count: int = 0
