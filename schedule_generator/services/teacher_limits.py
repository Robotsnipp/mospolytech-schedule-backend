"""Недельный лимит пар преподавателя (Teacher.max_pairs_per_week).

Решатель сначала размещает все обязательные пары, поэтому недельное число пар
преподавателя задаётся нагрузкой, а не выбором слотов: штраф в целевой функции
ничего бы не изменил. Лимит работает как проверка результата запуска: если
у преподавателя пар в неделю больше лимита, запуск получает предупреждение.

Считаются размещённые пары запуска и неподвижная занятость, которую видел
решатель: фиксированная ПД и (в запуске программы ДПО) пары основного
расписания и других программ. Одна неподвижная запись - одна пара недели.
"""

from __future__ import annotations

from collections import Counter

from ..models import Teacher


def weekly_teacher_load(assignments, specs, fixed_pd=()) -> Counter:
    """Пар в неделю на преподавателя: размещённые пары + неподвижная занятость."""
    teacher_by_event = {spec.event_id: spec.teacher_id for spec in specs}
    load = Counter()
    for assignment in assignments:
        teacher_id = teacher_by_event.get(assignment["event_id"])
        if teacher_id is not None:
            load[teacher_id] += 1
    for busy in fixed_pd:
        if busy.teacher_id is not None:
            load[busy.teacher_id] += 1
    return load


def weekly_limit_warnings(assignments, specs, fixed_pd=()) -> list[str]:
    """Тексты предупреждений для преподавателей сверх max_pairs_per_week."""
    load = weekly_teacher_load(assignments, specs, fixed_pd)
    if not load:
        return []
    warnings = []
    for teacher in Teacher.objects.filter(pk__in=load).order_by("full_name", "pk"):
        pairs = load[teacher.pk]
        if teacher.max_pairs_per_week and pairs > teacher.max_pairs_per_week:
            warnings.append(
                f"Преподаватель {teacher.full_name}: {pairs} пар в неделю при лимите "
                f"{teacher.max_pairs_per_week}. Перераспределите нагрузку или измените лимит.")
    return warnings
