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
    skipped = []
    for ev in events.select_related("group", "stream", "project", "teacher"):
        if ev.lesson_type in NON_GENERATED_LESSON_TYPES:
            # физкультура не генерируется: студент приходит в любое
            # время в любое подходящее место — это не событие расписания.
            continue

        fmt = ev.effective_format
        blocking: set[int] = set()
        blocking_students: set[int] = set()
        stream_id = None

        if ev.lesson_type == LessonType.LECTURE:
            if not ev.stream_id:
                skipped.append((ev, "Лекция без лекционного потока."))
                continue
            stream_id = ev.stream_id
            # все группы потока слушают лекцию -> они заблокированы
            blocking = set(ev.stream.groups.values_list("id", flat=True))
            audience = ev.audience_size or sum(
                g.students.count() for g in ev.stream.groups.all()
            )
        elif ev.project_id:
            if ev.lesson_type != LessonType.PD:
                skipped.append((ev, "Событие с проектом должно иметь тип ПД."))
                continue
            # ПД: группа проекта = студенты, выбравшие проект.
            # Блокируем именно этих студентов (а не целиком их учебные
            # группы), чтобы остальные студенты тех же групп могли учиться.
            blocking_students = set(
                Student.objects.filter(project_choice__project_id=ev.project_id)
                .values_list("id", flat=True)
            )
            audience = ev.audience_size or len(blocking_students)
            if not blocking_students:
                # никто не выбрал проект — планировать не для кого
                skipped.append((ev, "По проекту нет выбравших его студентов."))
                continue
        elif ev.group_id:
            blocking = {ev.group_id}
            audience = ev.audience_size
        else:
            skipped.append((ev, "У занятия нет носителя (группы/потока/проекта)."))
            continue

        specs.append(EventSpec(
            event_id=ev.id,
            lesson_type=ev.lesson_type,
            fmt=fmt,
            pairs_per_week=ev.pairs_per_week,
            audience_size=max(audience, 1),
            teacher_id=ev.teacher_id,
            blocking_group_ids=blocking,
            blocking_student_ids=blocking_students,
            stream_id=stream_id,
        ))
    return specs, skipped
