"""Отчёт о самочувствии групп по готовому запуску (фича «индекс усталости»).

Считает ту же усталость, что минимизирует решатель (services/wellbeing.py),
плюс окна. Отчёт охватывает учебные группы: пары ПД адресованы отдельным
студентам и в нём не учитываются.
"""

from __future__ import annotations

from collections import defaultdict

from ..models import ScheduledClass
from .config import FATIGUE_COMFORT_LEVEL
from .wellbeing import count_windows, day_fatigue, is_overloaded


def build_wellbeing_report(run) -> dict:
    """Усталость и окна по группам и дням запуска.

    :return: {"run", "comfort_level", "summary", "groups": [...]}; в группах
        только те, у кого в запуске есть пары.
    """
    lesson_numbers: dict[int, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    group_names: dict[int, str] = {}
    classes = (ScheduledClass.objects.filter(run=run)
               .select_related("slot", "event__group")
               .prefetch_related("event__stream__groups"))
    for scheduled in classes:
        event = scheduled.event
        if event.group_id is not None:
            holders = [event.group]
        elif event.stream_id is not None:
            holders = list(event.stream.groups.all())
        else:
            continue
        for group in holders:
            group_names[group.id] = group.name
            lesson_numbers[group.id][scheduled.slot.day_of_week].append(
                scheduled.slot.lesson_number)

    groups = []
    for group_id, numbers_by_day in sorted(lesson_numbers.items(),
                                           key=lambda item: group_names[item[0]]):
        days = []
        for day_of_week, numbers in sorted(numbers_by_day.items()):
            fatigue = day_fatigue(numbers)
            days.append({
                "day_of_week": day_of_week,
                "pairs": len(numbers),
                "windows": count_windows(numbers),
                "fatigue": fatigue,
                "overloaded": is_overloaded(fatigue),
            })
        groups.append({
            "group": group_id,
            "name": group_names[group_id],
            "max_fatigue": max(day["fatigue"] for day in days),
            "total_windows": sum(day["windows"] for day in days),
            "overloaded_days": sum(day["overloaded"] for day in days),
            "days": days,
        })

    return {
        "run": run.pk,
        "comfort_level": FATIGUE_COMFORT_LEVEL,
        "summary": {
            "groups": len(groups),
            "max_fatigue": max((group["max_fatigue"] for group in groups), default=0),
            "total_windows": sum(group["total_windows"] for group in groups),
            "overloaded_days": sum(group["overloaded_days"] for group in groups),
        },
        "groups": groups,
    }
