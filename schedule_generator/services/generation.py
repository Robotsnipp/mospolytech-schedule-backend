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

from django.core.exceptions import ValidationError
from django.db.models import Q

from ..enums import LessonType
from ..models import (
    FixedProjectSession,
    GenerationRun,
    LessonEvent,
    Room,
    Teacher,
    TimeSlot,
)
from .bells import validate_bell_pattern
from .config import PlanningPreferences, SolverConfig
from .curriculum import validate_prepared_load
from .dto import RunOutcome
from .event_specs import build_event_specs
from .fixed_pd import load_fixed_pd
from .generator import generate_schedule
from .result_writer import persist_result


class GenerationError(Exception):
    """Ошибка конфигурации запуска (например, у семестра нет шаблона недели)."""


def load_teacher_limits() -> dict[int, dict]:
    """Справка о преподавателях для мягких ограничений модели."""
    return {
        teacher.id: {"max_pairs_per_day": teacher.max_pairs_per_day,
                     "chronotype": teacher.chronotype}
        for teacher in Teacher.objects.all()
    }


def select_events(semester, module=None):
    """События периода: общие для семестра и, если задан модуль, его модульные."""
    events = LessonEvent.objects.filter(semester=semester)
    if module is not None:
        events = events.filter(Q(module=module) | Q(module__isnull=True))
    return events


def run_generation(*, semester, week_pattern=None, time_limit_seconds=30,
                   seed=17, clear_previous=True, module=None,
                   free_days_per_group=0, settings=None, group=None, resource_profile=None) -> RunOutcome:
    """Запускает полный цикл генерации недельного шаблона расписания.

    :param free_days_per_group: режим подготовки к сессии — сколько дней в
        неделю желательно оставить группам свободными (0 — режим выключен).
    :return: RunOutcome с id созданного GenerationRun и сводкой.
    :raises GenerationError: если семестру нечем строить расписание
        (нет WeekPattern) — run остаётся в статусе ERROR с сообщением.
    """
    # Callers can hold a Module instance across several module solves. Read its
    # actual calendar before freezing inputs for this particular new-mode run.
    if settings is not None and module is not None:
        module.refresh_from_db()
    if module is not None and module.semester_id != semester.id:
        raise GenerationError("Модуль относится к другому семестру.")
    if week_pattern is not None and week_pattern.semester_id != semester.id:
        raise GenerationError("Шаблон недели относится к другому семестру.")
    if module is None and LessonEvent.objects.filter(semester=semester, module__isnull=False).exists():
        raise GenerationError("Для занятий по модулям выберите модуль генерации.")
    if module is None and FixedProjectSession.objects.filter(project__semester=semester, module__isnull=False).exists():
        raise GenerationError("Для фиксированной ПД по модулям выберите модуль генерации.")
    if settings is not None:
        from .settings import normalize_settings
        try:
            settings = normalize_settings(settings)
            if group is None or not resource_profile:
                raise ValueError("Нужны выбранные группа и ресурсный профиль.")
            if group.semester_id != semester.pk:
                raise ValueError("Группа относится к другому периоду.")
            if not group.students.exists():
                raise ValueError("Нет состава выбранной группы: персональные правила проверить нельзя.")
        except ValueError as exc:
            raise GenerationError(str(exc)) from exc
        clear_previous = False
        time_limit_seconds = settings['search']['time_limit_seconds']
        seed = settings['search']['seed']
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
        rooms = list(Room.objects.filter(is_active=True, profile_only=False)
                     .select_related("building", "building__location"))  # правило локаций
        events = select_events(semester, module)
        room_snapshot = []
        if settings is not None:
            from .resource_profiles import resolve_resource_profile
            try:
                rooms, room_snapshot = resolve_resource_profile(resource_profile, semester, group)
            except (ValidationError, ValueError) as exc:
                raise GenerationError(str(exc)) from exc
            events = events.filter(Q(group=group) | Q(stream__groups=group) | Q(project__enrollments__student__group=group)).distinct()
            if not events.exists():
                raise GenerationError("Для выбранной группы нет подготовленной нагрузки.")
            for event in events.filter(stream__isnull=False).prefetch_related('stream__groups'):
                if set(event.stream.groups.values_list('id', flat=True)) != {group.pk}:
                    raise GenerationError("Поток нескольких групп не поддерживается новым режимом.")

        # --- 4. подготовка событий ---
        try:
            validate_bell_pattern(week_pattern, slots, events)
            if settings is not None:
                from .settings import validate_settings_grid
                validate_settings_grid(slots)
            validate_prepared_load(events)
            fixed_pd, snapshot = load_fixed_pd(semester=semester, module=module,
                                                week_pattern=week_pattern, slots=slots)
        except (ValidationError, ValueError) as exc:
            raise GenerationError(str(exc)) from exc
        run.fixed_pd_snapshot = snapshot
        # Фиксированная сетка проекта заменяет потребность генерировать его ПД,
        # в том числе когда все даты проекта вне выбранного модуля/чётности.
        fixed_projects = FixedProjectSession.objects.filter(project__semester=semester).values("project_id")
        events = events.exclude(lesson_type=LessonType.PD, project_id__in=fixed_projects)
        specs, skipped = build_event_specs(events)

        if settings is not None:
            from .planning_snapshot import build_snapshot
            # New policy explicitly controls Sunday; the legacy allowed_days are not copied as hidden bans.
            for spec in specs:
                if spec.lesson_type != LessonType.PD:
                    spec.allowed_days = frozenset(range(7))
            run.settings_snapshot = settings
            run.input_snapshot = build_snapshot(group=group, settings=settings, slots=slots, specs=specs,
                events=events, rooms=room_snapshot, fixed_pd=fixed_pd, semester=semester,
                module=module, week_pattern=week_pattern)
            run.input_snapshot['resource_profile'] = resource_profile
            building_rule = settings['rules'].get('preferred_building')
            if building_rule and building_rule['value'] not in {room.get('building_id') for room in room_snapshot}:
                raise GenerationError("Выбранный корпус не входит в ресурсный профиль.")

        # --- 5. решение ---
        solver_config = SolverConfig(time_limit_seconds=time_limit_seconds, seed=seed)
        if settings is not None:
            from .settings_solver import solve_settings
            try:
                generation_result = solve_settings(slots=slots, rooms=rooms, specs=specs,
                    fixed_pd=fixed_pd, snapshot=run.input_snapshot, config=solver_config)
            except ValueError as exc:
                raise GenerationError(str(exc)) from exc
            from .placement_validation import validate_placements
            run.validation_snapshot = validate_placements(run.input_snapshot, generation_result.assignments)
            if generation_result.status in ('SOLVED', 'PARTIAL') and not run.validation_snapshot['valid']:
                raise GenerationError("Результат не прошёл серверную проверку: " + str(run.validation_snapshot['blocking']))
        else:
            generation_result = generate_schedule(
                slots=slots, rooms=rooms, specs=specs,
                teachers_by_id=load_teacher_limits(), config=solver_config,
                fixed_pd=fixed_pd,
                preferences=PlanningPreferences(free_days_per_group=free_days_per_group),
            )

        # --- 6. сохранение ---
        persist_result(run=run, generation_result=generation_result,
                       slots=slots, rooms=rooms, events=events,
                       skipped=skipped, clear_previous=clear_previous)
    except GenerationError as exc:
        exc.run_id = run.pk
        run.status = "ERROR"
        run.message = str(exc)
        run.save()
        raise
    except Exception as exc:  # noqa: BLE001 — фиксируем сбой в истории запусков
        exc.run_id = run.pk
        run.status = "ERROR"
        run.message = str(exc)
        run.save()
        raise
    return RunOutcome(
        run_id=run.pk,
        status=run.status,
        scheduled_count=run.scheduled_classes.count(),
        issue_count=run.issues.count(),
        fixed_pd_count=len(run.fixed_pd_snapshot),
    )
