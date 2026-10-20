"""Самовосстановление расписания: минимальная перестройка после сбоя.

Сбой — преподаватель недоступен в некоторые дни недели либо аудитория закрыта.
Вместо полной перегенерации строится новый запуск, где все пары, которых сбой
не касается, закреплены (мягко, через PIN_BONUS), а затронутые ищут новое место.
Исходный запуск не меняется: результат — отдельный GenerationRun, который
можно сравнить со старым.

Оркестрация та же, что у ``run_generation``: данные -> specs -> решатель ->
``persist_result``; отличие в закреплённых парах и запрещённых размещениях.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db.models import Q

from ..enums import LessonType
from ..models import (
    FixedProjectSession,
    GenerationRun,
    Room,
    ScheduledClass,
    Student,
    TimeSlot,
)
from .config import SolverConfig
from .curriculum import validate_prepared_load
from .event_specs import build_event_specs
from .fixed_pd import load_fixed_pd
from .generation import load_teacher_limits, select_events
from .generator import generate_schedule
from .result_writer import persist_result
from .schedule_queries import SUCCESSFUL_STATUSES

VALID_DAYS = frozenset(range(6))


class ReplanError(Exception):
    """Сбой задан некорректно или не затрагивает ни одной пары."""


@dataclass
class ReplanOutcome:
    """Итог перепланирования: новый запуск и что в нём изменилось."""

    run_id: int
    status: str
    affected_count: int            # пар, которые сбой заставил искать новое место
    unchanged_count: int           # пар, оставшихся ровно там же (слот и аудитория)
    affected_students: int         # студентов, чьё расписание изменилось
    changes: list[dict] = field(default_factory=list)


def replan_after_disruption(*, run, teacher=None, unavailable_days=None, room=None,
                            time_limit_seconds: int = 30, seed: int = 17) -> ReplanOutcome:
    """Перестраивает расписание запуска ``run`` после сбоя.

    Ровно один из двух сбоев:
    - ``teacher`` + ``unavailable_days`` (номера дней 0=пн … 5=сб): преподаватель
      не может вести занятия в эти дни, его пары в такие дни переносятся;
    - ``room``: аудитория закрыта, пары в ней получают другую аудиторию или слот.

    :raises ReplanError: некорректные параметры, неудачный исходный запуск или
        сбой, не затрагивающий ни одной пары.
    """
    unavailable_days = set(unavailable_days or ())
    _validate(run=run, teacher=teacher, unavailable_days=unavailable_days, room=room)

    old_classes = list(ScheduledClass.objects.filter(run=run).select_related("slot", "event"))
    affected = [scheduled for scheduled in old_classes
                if _is_affected(scheduled, teacher, unavailable_days, room)]
    if not affected:
        raise ReplanError("Сбой не затрагивает ни одной пары запуска.")
    affected_ids = {scheduled.id for scheduled in affected}

    week_pattern = (old_classes[0].slot.week_pattern if old_classes
                    else run.semester.week_patterns.order_by("id").first())
    slots = list(TimeSlot.objects.filter(week_pattern=week_pattern)
                 .order_by("day_of_week", "lesson_number"))
    available_rooms = Room.objects.filter(is_active=True)
    if room is not None:
        available_rooms = available_rooms.exclude(id=room.id)
    rooms = list(available_rooms.select_related("building", "building__location"))
    events = select_events(run.semester, run.module)
    try:
        validate_prepared_load(events)
        fixed_pd, snapshot = load_fixed_pd(semester=run.semester, module=run.module,
                                          week_pattern=week_pattern, slots=slots)
    except (ValidationError, ValueError) as exc:
        raise ReplanError(str(exc)) from exc
    if any((teacher is not None and session.teacher_id == teacher.pk
            and session.day_of_week in unavailable_days)
           or (room is not None and session.room_id == room.pk) for session in fixed_pd):
        raise ReplanError("Сбой затрагивает фиксированную ПД: сначала исправьте её источник.")
    fixed_projects = FixedProjectSession.objects.filter(project__semester=run.semester).values("project_id")
    events = events.exclude(lesson_type=LessonType.PD, project_id__in=fixed_projects)
    specs, skipped = build_event_specs(events)

    pinned = [{"event_id": scheduled.event_id, "slot_id": scheduled.slot_id,
               "room_id": scheduled.room_id}
              for scheduled in old_classes if scheduled.id not in affected_ids]
    forbidden = _forbidden_placements(specs, slots, teacher, unavailable_days)

    note = f"Перепланирование запуска #{run.pk}: {_describe(teacher, unavailable_days, room)}."
    new_run = GenerationRun.objects.create(
        semester=run.semester, module=run.module, status="PENDING", message=note,
        fixed_pd_snapshot=snapshot)
    try:
        result = generate_schedule(
            slots=slots, rooms=rooms, specs=specs, teachers_by_id=load_teacher_limits(),
            config=SolverConfig(time_limit_seconds=time_limit_seconds, seed=seed),
            pinned=pinned, forbidden=forbidden, fixed_pd=fixed_pd)
        persist_result(run=new_run, generation_result=result, slots=slots, rooms=rooms,
                       events=events, skipped=skipped, clear_previous=False)
        # persist_result записывает сообщение решателя; пометка о причине запуска не должна теряться
        new_run.message = f"{note} {new_run.message}"
        new_run.save(update_fields=["message"])
    except Exception as exc:  # noqa: BLE001 — фиксируем сбой в истории запусков
        new_run.status = "ERROR"
        new_run.message = str(exc)
        new_run.save()
        raise

    new_classes = list(ScheduledClass.objects.filter(run=new_run).select_related("slot"))
    changes, unchanged_count, changed_event_ids = _diff(old_classes, new_classes)
    return ReplanOutcome(
        run_id=new_run.pk, status=new_run.status, affected_count=len(affected),
        unchanged_count=unchanged_count,
        affected_students=_count_students(specs, changed_event_ids), changes=changes)


def _validate(*, run, teacher, unavailable_days, room) -> None:
    if run.status not in SUCCESSFUL_STATUSES:
        raise ReplanError("Перепланировать можно только завершённый запуск (SOLVED или PARTIAL).")
    if (teacher is None) == (room is None):
        raise ReplanError("Укажите ровно один сбой: преподавателя или аудиторию.")
    if teacher is not None:
        if not unavailable_days:
            raise ReplanError("Для преподавателя укажите дни недели, когда он недоступен.")
        if not unavailable_days <= VALID_DAYS:
            raise ReplanError("Дни недели должны быть числами от 0 (пн) до 5 (сб).")
    elif unavailable_days:
        raise ReplanError("Дни недели задаются только для сбоя преподавателя.")


def _is_affected(scheduled, teacher, unavailable_days, room) -> bool:
    if teacher is not None:
        return (scheduled.event.teacher_id == teacher.id
                and scheduled.slot.day_of_week in unavailable_days)
    return scheduled.room_id == room.id


def _forbidden_placements(specs, slots, teacher, unavailable_days) -> set[tuple[int, int]]:
    if teacher is None:
        return set()
    return {(spec.event_id, slot.id)
            for spec in specs if spec.teacher_id == teacher.id
            for slot in slots if slot.day_of_week in unavailable_days}


def _describe(teacher, unavailable_days, room) -> str:
    if teacher is not None:
        days = ", ".join(str(day) for day in sorted(unavailable_days))
        return f"преподаватель «{teacher}» недоступен в дни {days}"
    return f"аудитория «{room}» закрыта"


def _diff(old_classes, new_classes) -> tuple[list[dict], int, set[int]]:
    """Сравнивает запуски по событиям: что убрано, что добавлено, сколько не тронуто."""
    def by_event(classes):
        grouped: dict[int, set[tuple[int, int, int | None]]] = {}
        for scheduled in classes:
            grouped.setdefault(scheduled.event_id, set()).add(
                (scheduled.slot.day_of_week, scheduled.slot.lesson_number, scheduled.room_id))
        return grouped

    old, new = by_event(old_classes), by_event(new_classes)
    changes = []
    unchanged_count = 0
    changed_event_ids: set[int] = set()
    for event_id in sorted(old.keys() | new.keys()):
        before, after = old.get(event_id, set()), new.get(event_id, set())
        unchanged_count += len(before & after)
        if before != after:
            changed_event_ids.add(event_id)
            changes.append({
                "event": event_id,
                "removed": [_placement(item) for item in sorted(before - after)],
                "added": [_placement(item) for item in sorted(after - before)],
            })
    return changes, unchanged_count, changed_event_ids


def _placement(item: tuple[int, int, int | None]) -> dict:
    day_of_week, lesson_number, room_id = item
    return {"day_of_week": day_of_week, "lesson_number": lesson_number, "room": room_id}


def _count_students(specs, event_ids: set[int]) -> int:
    """Студентов, у которых изменилось хотя бы одно занятие."""
    group_ids: set[int] = set()
    student_ids: set[int] = set()
    for spec in specs:
        if spec.event_id in event_ids:
            group_ids |= spec.blocking_group_ids
            student_ids |= spec.blocking_student_ids
    return Student.objects.filter(Q(group_id__in=group_ids) | Q(id__in=student_ids)).count()
