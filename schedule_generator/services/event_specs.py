"""Подготовка событий к генерации: LessonEvent (ORM) -> EventSpec (модель).

Это единственный модуль генерации, который читает ORM-данные о занятиях.
Правила отбора/валидации событий сосредоточены здесь; при добавлении нового
типа занятия правки сводятся к этому файлу + enums.py (+ ограничения модели,
если меняется семантика блокировок).

Логика выбора носителя и блокировок:
- LECTURE  — носитель stream: блокируются ВСЕ группы потока, аудитория не нужна;
- PD       — носитель project: блокируются только студенты, выбравшие проект
             (остальные студенты их учебных групп свободны в этот слот);
- остальные— носитель group: блокируется одна учебная группа.
Физкультура (PE) исключается из генерации полностью (NON_GENERATED_LESSON_TYPES).
"""

from __future__ import annotations

from ..enums import NON_GENERATED_LESSON_TYPES, LessonType
from ..models import Student
from .dto import EventSpec


def build_event_specs(events) -> tuple[list[EventSpec], list[tuple]]:
    """Конвертирует queryset LessonEvent в specs, отбрасывая негенерируемые
    типы (физкультура) и события без корректного носителя.

    Возвращает (specs, skipped), где skipped — список пар
    (event, причина пропуска); причины уходят в GenerationIssue.
    """
    specs: list[EventSpec] = []
    skipped: list[tuple] = []
    for event in events.select_related("group", "stream", "project", "teacher"):
        if event.lesson_type in NON_GENERATED_LESSON_TYPES:
            # физкультура не генерируется: студент приходит в любое
            # время в любое подходящее место — это не событие расписания.
            continue

        lesson_format = event.effective_format
        blocking_group_ids: set[int] = set()
        blocking_student_ids: set[int] = set()
        stream_id = None

        if event.lesson_type == LessonType.LECTURE:
            if not event.stream_id:
                skipped.append((event, "Лекция без лекционного потока."))
                continue
            stream_id = event.stream_id
            # все группы потока слушают лекцию -> они заблокированы
            blocking_group_ids = set(event.stream.groups.values_list("id", flat=True))
            audience_size = event.audience_size or sum(
                group.students.count() for group in event.stream.groups.all()
            )
        elif event.project_id:
            if event.lesson_type != LessonType.PD:
                skipped.append((event, "Событие с проектом должно иметь тип ПД."))
                continue
            # ПД: группа проекта = студенты, выбравшие проект.
            # Блокируем именно этих студентов (а не целиком их учебные
            # группы), чтобы остальные студенты тех же групп могли учиться.
            blocking_student_ids = set(
                Student.objects.filter(project_choice__project_id=event.project_id)
                .values_list("id", flat=True)
            )
            audience_size = event.audience_size or len(blocking_student_ids)
            if not blocking_student_ids:
                # никто не выбрал проект — планировать не для кого
                skipped.append((event, "По проекту нет выбравших его студентов."))
                continue
        elif event.group_id:
            blocking_group_ids = {event.group_id}
            audience_size = event.audience_size
        else:
            skipped.append((event, "У занятия нет носителя (группы/потока/проекта)."))
            continue

        specs.append(EventSpec(
            event_id=event.id,
            lesson_type=event.lesson_type,
            format=lesson_format,
            pairs_per_week=event.pairs_per_week,
            audience_size=max(audience_size, 1),
            teacher_id=event.teacher_id,
            blocking_group_ids=blocking_group_ids,
            blocking_student_ids=blocking_student_ids,
            stream_id=stream_id,
        ))
    return specs, skipped
