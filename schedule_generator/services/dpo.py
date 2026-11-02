"""Программы ДПО: подготовка нагрузки и планирование поверх основного расписания.

Поток данных:

    DPOProgram + DPODiscipline          — что и в каком объёме преподавать
        │  prepare_program_load
        ▼
    LessonEvent (lesson_type=DPO)       — пар в неделю на каждую группу ДПО
        │  plan_program
        ▼
    run_generation(only_events=..., background_busy=..., dpo_program=...)
        — CP-SAT размещает только пары программы; пары основного запуска,
          фиксированная ПД и уже спланированные другие программы ДПО — это
          неподвижная занятость студентов, преподавателей и аудиторий.

Основное расписание не изменяется: результат — отдельный GenerationRun со
ссылкой на программу.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from ..enums import LessonType
from ..models import (
    DPOMembership,
    DPOProgram,
    GenerationRun,
    LessonEvent,
    ProjectEnrollment,
    ScheduledClass,
    Student,
)
from ..models.dpo import ACADEMIC_HOURS_PER_PAIR
from .dto import FixedPDSpec, RunOutcome
from .generation import GenerationError, run_generation
from .schedule_queries import SUCCESSFUL_STATUSES, resolve_run


class DPOPlanningError(ValueError):
    """Программу нельзя подготовить или спланировать; текст - для человека."""


def _minute(value) -> int:
    return value.hour * 60 + value.minute


def program_events(program: DPOProgram):
    return LessonEvent.objects.filter(semester=program.semester,
                                      preparation_source__dpo_program=program.pk)


def program_members(group) -> set[int]:
    """Слушатели группы ДПО: студенты основного обучения и прямой состав группы."""
    ids = set(group.dpo_memberships.values_list("student_id", flat=True))
    ids.update(group.students.values_list("id", flat=True))
    return ids


@transaction.atomic
def prepare_program_load(program: DPOProgram) -> list[LessonEvent]:
    """Создаёт или обновляет LessonEvent программы: дисциплина × группа ДПО.

    Общий объём переводится в пары в неделю без округления: при остатке -
    ошибка с подсказкой, как в подготовке учебных планов.
    """
    program.full_clean()
    weeks = program.effective_weeks
    if not weeks:
        raise DPOPlanningError("Укажите число учебных недель программы или модуль.")
    groups = list(program.groups.order_by("name"))
    if not groups:
        raise DPOPlanningError("В программе нет групп ДПО.")
    disciplines = list(program.disciplines.select_related("subject", "teacher").order_by("id"))
    if not disciplines:
        raise DPOPlanningError("В программе нет дисциплин.")

    errors = []
    for group in groups:
        if not group.is_dpo:
            errors.append(f"Группа {group.name} не отмечена как группа ДПО.")
        elif not program_members(group):
            errors.append(f"В группе {group.name} нет слушателей.")
    for discipline in disciplines:
        discipline.full_clean()
        pairs = discipline.pairs_total
        if pairs % weeks or pairs == 0:
            errors.append(
                f"{discipline.subject}: {pairs} пар(ы) не делятся на {weeks} недель без остатка. "
                f"Подходящий объём: {max(pairs // weeks, 1) * weeks} или "
                f"{(pairs // weeks + 1) * weeks} пар.")
    if errors:
        raise DPOPlanningError(" ".join(errors))

    kept_ids = []
    for group in groups:
        audience = len(program_members(group))
        for discipline in disciplines:
            pairs = discipline.pairs_total
            source = {"dpo_program": program.pk, "dpo_discipline": discipline.pk,
                      "pairs_total": pairs, "weeks": weeks}
            event = (program_events(program)
                     .filter(group=group, preparation_source__dpo_discipline=discipline.pk)
                     .first()) or LessonEvent(semester=program.semester, group=group,
                                              lesson_type=LessonType.DPO)
            event.subject = discipline.subject
            event.teacher = discipline.teacher
            event.format = discipline.format
            event.module = program.module
            event.pairs_per_week = pairs // weeks
            event.academic_hours = pairs * ACADEMIC_HOURS_PER_PAIR
            event.audience_size = audience
            event.preparation_source = source
            event.full_clean()
            event.save()
            kept_ids.append(event.pk)

    stale = program_events(program).exclude(pk__in=kept_ids)
    if stale.filter(classes__isnull=False).exists():
        raise DPOPlanningError(
            "Нельзя убрать дисциплину или группу: её пары уже есть в запусках программы. "
            "Удалите эти запуски или верните дисциплину.")
    stale.delete()
    return list(program_events(program).order_by("id"))


def resolve_base_run(program: DPOProgram) -> GenerationRun | None:
    if program.base_run_id:
        if program.base_run.status not in SUCCESSFUL_STATUSES:
            raise DPOPlanningError("Основной запуск не завершён успешно (нужен SOLVED или PARTIAL).")
        return program.base_run
    return resolve_run(program.semester, program.module)


def _other_program_runs(program: DPOProgram) -> list[GenerationRun]:
    """Последний успешный запуск каждой другой программы этого же периода."""
    runs = (GenerationRun.objects
            .filter(semester=program.semester, module=program.module,
                    status__in=SUCCESSFUL_STATUSES, dpo_program__isnull=False)
            .exclude(dpo_program=program).order_by("dpo_program_id", "-created_at", "-pk"))
    latest = {}
    for run in runs:
        latest.setdefault(run.dpo_program_id, run)
    return list(latest.values())


def _class_people(event, members_by_dpo_group, students_by_group, students_by_project):
    """Студенты и учебные группы, занятые парой основного (или другого ДПО) запуска."""
    if event.stream_id:
        group_ids = {group.pk for group in event.stream.groups.all()}
    elif event.group_id:
        group_ids = {event.group_id}
    else:
        group_ids = set()
    students = set()
    for group_id in group_ids:
        students |= students_by_group.get(group_id, set())
        students |= members_by_dpo_group.get(group_id, set())
    if event.project_id:
        students |= students_by_project.get(event.project_id, set())
    return students, group_ids


def load_background_busy(runs, week_pattern) -> tuple[list[FixedPDSpec], list[dict]]:
    """Пары запусков как неподвижная занятость шаблона недели ``week_pattern``."""
    classes = list(ScheduledClass.objects
                   .filter(run__in=runs, slot__week_pattern=week_pattern)
                   .select_related("slot", "event", "event__stream", "event__project")
                   .prefetch_related("event__stream__groups"))
    group_ids = set()
    for scheduled in classes:
        if scheduled.event.group_id:
            group_ids.add(scheduled.event.group_id)
        if scheduled.event.stream_id:
            group_ids |= {group.pk for group in scheduled.event.stream.groups.all()}
    students_by_group: dict[int, set[int]] = {}
    for student_id, group_id in Student.objects.filter(group_id__in=group_ids).values_list("id", "group_id"):
        students_by_group.setdefault(group_id, set()).add(student_id)
    members_by_dpo_group: dict[int, set[int]] = {}
    for student_id, group_id in DPOMembership.objects.filter(group_id__in=group_ids).values_list(
            "student_id", "group_id"):
        members_by_dpo_group.setdefault(group_id, set()).add(student_id)
    project_ids = {scheduled.event.project_id for scheduled in classes if scheduled.event.project_id}
    students_by_project: dict[int, set[int]] = {}
    for student_id, project_id in ProjectEnrollment.objects.filter(project_id__in=project_ids).values_list(
            "student_id", "project_id"):
        students_by_project.setdefault(project_id, set()).add(student_id)

    busy, summary = [], []
    for scheduled in classes:
        slot = scheduled.slot
        if slot.start_time is None or slot.end_time is None:
            raise DPOPlanningError("У слотов основного расписания нет времени начала и конца.")
        students, groups = _class_people(scheduled.event, members_by_dpo_group,
                                         students_by_group, students_by_project)
        teacher_id = scheduled.event.teacher_id
        if scheduled.event.project_id and teacher_id is None:
            teacher_id = scheduled.event.project.supervisor_id
        busy.append(FixedPDSpec(
            session_id=-scheduled.pk, project_id=0, day_of_week=slot.day_of_week,
            start_minute=_minute(slot.start_time), end_minute=_minute(slot.end_time),
            teacher_id=teacher_id, student_ids=frozenset(students),
            participant_group_ids=frozenset(groups), room_id=scheduled.room_id,
            place_raw="", travel_buffer_minutes=0, occurrence_dates=frozenset(),
        ))
        summary.append({"run_id": scheduled.run_id, "class_id": scheduled.pk, "slot_id": slot.pk})
    return busy, summary


def _week_pattern_for(program: DPOProgram, base_run: GenerationRun | None):
    if base_run is not None:
        scheduled = base_run.scheduled_classes.select_related("slot__week_pattern").first()
        if scheduled is not None:
            return scheduled.slot.week_pattern
    pattern = program.semester.week_patterns.order_by("id").first()
    if pattern is None:
        raise DPOPlanningError("У семестра нет шаблона недели.")
    return pattern


def plan_program(program: DPOProgram, *, time_limit_seconds: int = 30, seed: int = 17) -> dict:
    """Готовит нагрузку и размещает пары программы, не трогая основное расписание."""
    events = prepare_program_load(program)
    base_run = resolve_base_run(program)
    week_pattern = _week_pattern_for(program, base_run)
    runs = ([base_run] if base_run else []) + _other_program_runs(program)
    busy, summary = load_background_busy(runs, week_pattern)
    try:
        outcome: RunOutcome = run_generation(
            semester=program.semester, module=program.module, week_pattern=week_pattern,
            time_limit_seconds=time_limit_seconds, seed=seed, clear_previous=False,
            only_events=LessonEvent.objects.filter(pk__in=[event.pk for event in events]),
            background_busy=busy, dpo_program=program)
    except (GenerationError, ValidationError) as exc:
        raise DPOPlanningError(str(exc)) from exc
    return {
        "run_id": outcome.run_id,
        "status": outcome.status,
        "scheduled_count": outcome.scheduled_count,
        "required_count": sum(event.pairs_per_week for event in events),
        "issue_count": outcome.issue_count,
        "base_run_id": base_run.pk if base_run else None,
        "other_dpo_run_ids": [run.pk for run in runs if run.dpo_program_id],
        "background_classes": len(summary),
    }
