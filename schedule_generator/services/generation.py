"""Оркестрация генерации расписания — ЕДИНСТВЕННАЯ точка входа.

Любой код (HTTP view, management command, фоновая задача Celery) должен
запускать генерацию только через ``run_generation``. View-слой остаётся тонким,
а правила жизненного цикла запуска собраны в одном месте.

Полный цикл (шаги читаются сверху вниз):

    1. создать GenerationRun (PENDING)                     — история запусков
    2. выбрать шаблон недели (week_pattern)                — models/calendar.py
    3. загрузить данные: слоты, аудитории, события         — queryset'ы
    4. подготовить specs: LessonEvent -> EventSpec         — services/event_specs.py
       (физкультура исключается; кривые события -> skipped)
    5. решить задачу CP-SAT                                — services/generator.py
    6. атомарно сохранить результат                        — services/result_writer.py
       (ScheduledClass + GenerationIssue + финальный статус run)
    7. при любой ошибке пометить run ERROR и пробросить исключение

Как расширять:
- новое правило отбора занятий        -> services/event_specs.py
- новое жёсткое ограничение модели    -> services/constraints.py
- новый штраф/вес целевой функции     -> services/objectives.py (+ config.py)
- новый параметр решателя             -> services/config.py (SolverConfig)
"""

from __future__ import annotations

from ..models import GenerationRun, LessonEvent, Room, Teacher, TimeSlot
from .config import SolverConfig
from .dto import RunOutcome
from .event_specs import build_event_specs
from .generator import generate_schedule
from .result_writer import persist_result


class GenerationError(Exception):
    """Ошибка конфигурации запуска (например, у семестра нет шаблона недели)."""


def load_teacher_limits() -> dict[int, dict]:
    """Справка о преподавателях для мягких ограничений модели."""
    return {
        teacher.id: {"max_pairs_per_day": teacher.max_pairs_per_day}
        for teacher in Teacher.objects.all()
    }


def run_generation(*, semester, week_pattern=None, time_limit_seconds=30,
                   seed=17, clear_previous=True, module=None) -> RunOutcome:
    """Запускает полный цикл генерации недельного шаблона расписания.

    :return: RunOutcome с id созданного GenerationRun и сводкой.
    :raises GenerationError: если семестру нечем строить расписание
        (нет WeekPattern) — run остаётся в статусе ERROR с сообщением.
    """
    if module is not None and module.semester_id != semester.id:
        raise GenerationError("Модуль относится к другому семестру.")
    if week_pattern is not None and week_pattern.semester_id != semester.id:
        raise GenerationError("Шаблон недели относится к другому семестру.")
    if module is None and LessonEvent.objects.filter(semester=semester, module__isnull=False).exists():
        raise GenerationError("Для занятий по модулям выберите модуль генерации.")
    run = GenerationRun.objects.create(semester=semester, module=module, status="PENDING")
    try:
        # --- 2. шаблон недели ---
        if week_pattern is None:
            week_pattern = semester.week_patterns.order_by("id").first()
        if week_pattern is None:
            run.status = "ERROR"
            run.message = "У семестра нет ни одного шаблона недели (TimeSlot)."
            run.save()
            raise GenerationError(run.message)

        # --- 3. данные ---
        slots = list(TimeSlot.objects.filter(week_pattern=week_pattern)
                     .order_by("day_of_week", "lesson_number"))
        rooms = list(Room.objects.filter(is_active=True)
                     .select_related("building"))  # building.campus — правило кампусов
        events = LessonEvent.objects.filter(semester=semester)
        if module is not None:
            from django.db.models import Q
            events = events.filter(Q(module=module) | Q(module__isnull=True))

        # --- 4. подготовка событий ---
        specs, skipped = build_event_specs(events)

        # --- 5. решение ---
        solver_config = SolverConfig(time_limit_seconds=time_limit_seconds, seed=seed)
        generation_result = generate_schedule(
            slots=slots, rooms=rooms, specs=specs,
            teachers_by_id=load_teacher_limits(), config=solver_config,
        )

        # --- 6. сохранение ---
        persist_result(run=run, generation_result=generation_result,
                       slots=slots, rooms=rooms, events=events,
                       specs=specs, skipped=skipped, clear_previous=clear_previous)
    except GenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 — фиксируем сбой в истории запусков
        run.status = "ERROR"
        run.message = str(exc)
        run.save()
        raise
    return RunOutcome(
        run_id=run.pk,
        status=run.status,
        scheduled_count=run.scheduled_classes.count(),
        issue_count=run.issues.count(),
    )
