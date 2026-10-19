"""Совместные окна студентов (фича «встретиться с друзьями»).

Без состояния: список студентов передаётся в запросе. Занятость берётся из
готового расписания (пары группы, лекции потоков, ПД выбранного проекта).
"""

from __future__ import annotations

from ..models import Student, TimeSlot
from .schedule_queries import scheduled_classes_for, student_holder_filter


def common_free_slots(*, semester, students: list[Student], module=None, run_id=None,
                      week_pattern=None) -> list[TimeSlot]:
    """Слоты шаблона недели, в которых свободны все перечисленные студенты.

    :param week_pattern: шаблон недели; по умолчанию первый шаблон семестра
        (как при генерации). Если шаблона нет, окон нет.
    """
    if week_pattern is None:
        week_pattern = semester.week_patterns.order_by("id").first()
    if week_pattern is None:
        return []

    busy: set[tuple[int, int]] = set()
    for student in students:
        classes = scheduled_classes_for(
            semester, student_holder_filter(student), module=module, run_id=run_id)
        busy.update((scheduled.slot.day_of_week, scheduled.slot.lesson_number)
                    for scheduled in classes)

    return [slot for slot in TimeSlot.objects.filter(week_pattern=week_pattern)
            .order_by("day_of_week", "lesson_number")
            if (slot.day_of_week, slot.lesson_number) not in busy]
