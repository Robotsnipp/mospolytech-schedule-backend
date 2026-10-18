--- dev_dashboard/student_view.py (原始)
"""Представление расписания «глазами студента» для dev-панели.

Расписание собирается из результата последнего GenerationRun:
- практика/лаборатория/внеучебка/ДПО — пары носителя (группы), в котором
  числится студент;
- лекция — пары потока, куда входит группа студента;
- ПД — пары проекта, который студент выбрал (ProjectEnrollment);
- физкультура (PE) не генерируется и показывается отдельной пометкой.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Q

from schedule_generator.enums import LessonType, NON_GENERATED_LESSON_TYPES, effective_format
from schedule_generator.models import (
    GenerationRun,
    LectureStream,
    LessonEvent,
    ProjectEnrollment,
    ScheduledClass,
    Student,
    TimeSlot,
)

DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]


def get_latest_run() -> GenerationRun | None:
    return GenerationRun.objects.order_by("-created_at").first()


def _build_lesson_map(run: GenerationRun, group_ids: set[int],
                      enrollment_project_ids: set[int]) -> dict[int, list[dict]]:
    """slot_id -> список занятий, релевантных носителю студента.

    Носители: учебная группа студента (включая группу ДПО), её лекционные
    потоки и выбранные студентом проекты ПД.
    """
    stream_ids = list(LectureStream.objects.filter(groups__in=group_ids)
                      .values_list("id", flat=True))
    holder_filter = (
        Q(event__group_id__in=group_ids)
        | Q(event__stream_id__in=stream_ids)
        | Q(event__project_id__in=enrollment_project_ids)
    )
    classes = (ScheduledClass.objects
               .filter(run=run)
               .filter(holder_filter)
               .exclude(event__lesson_type__in=NON_GENERATED_LESSON_TYPES)
               .select_related("slot", "room", "event__subject",
                               "event__teacher", "event__group", "event__stream"))
    by_slot: dict[int, list[dict]] = {}
    for sc in classes:
        event = sc.event
        fmt = effective_format(event.lesson_type, event.format)
        by_slot.setdefault(sc.slot_id, []).append({
            "subject": event.subject.name,
            "lesson_type_display": event.get_lesson_type_display(),
            "teacher": event.teacher.full_name if event.teacher_id else "—",
            "room": sc.room.name if sc.room_id else ("онлайн" if fmt == "ONLINE" else "—"),
            "is_online": fmt == "ONLINE",
            "highlight": event.lesson_type == LessonType.PD,
        })
    return by_slot


@dataclass
class StudentSchedule:
    student: Student
    rows: list[list[list[dict]]] = field(default_factory=list)  # [номер пары][день]
    num_days: int = 0
    total_pairs: int = 0
    pe_note: str = ""

    @property
    def grid(self) -> list[dict]:
        """Сетка для шаблона: строки (номера пар) с ячейками по дням недели.

        Каждая строка — {'lesson': номер_пары, 'cells': [ячейка_дня, ...]},
        где ячейка — {'lessons': [...]} либо None (окно/нет слота).
        """
        out = []
        for r, row in enumerate(self.rows):
            out.append({
                "lesson": r + 1,
                "cells": [{"lessons": cell} if cell else None for cell in row],
            })
        return out


def build_student_schedule(run: GenerationRun, student: Student) -> StudentSchedule:
    group_ids = {student.group_id}
    enrollment = ProjectEnrollment.objects.filter(student=student).first()
    project_ids = {enrollment.project_id} if enrollment else set()

    lesson_map = _build_lesson_map(run, group_ids, project_ids)

    slots = list(TimeSlot.objects
                 .filter(week_pattern__semester=run.semester)
                 .order_by("day_of_week", "lesson_number"))
    max_day = max((s.day_of_week for s in slots), default=0)
    max_lesson = max((s.lesson_number for s in slots), default=0)
    # days[день][номер пары] — список занятий в ячейке.
    days: list[list[list[dict]]] = [[[] for _ in range(max_lesson)]
                                    for _ in range(max_day + 1)]
    total = 0
    for slot in slots:
        lessons = lesson_map.get(slot.id)
        if not lessons:
            continue
        days[slot.day_of_week][slot.lesson_number - 1].extend(lessons)
        total += len(lessons)

    pe_events = (LessonEvent.objects
                 .filter(semester=run.semester, group_id__in=group_ids,
                         lesson_type=LessonType.PE)
                 .select_related("subject"))
    pe_note = ""
    if pe_events.exists():
        names = ", ".join(e.subject.name for e in pe_events)
        pe_note = (f"Физкультура ({names}) в расписании не участвует: "
                   f"{sum(e.pairs_per_week for e in pe_events)} пар/нед — свободно.")

    # Транспонируем: строки таблицы — номера пар, колонки — дни недели.
    rows = [[days[d][l] for d in range(max_day + 1)] for l in range(max_lesson)]
    return StudentSchedule(student=student, rows=rows, num_days=max_day + 1,
                           total_pairs=total, pe_note=pe_note)


def pick_representative_students(run: GenerationRun, limit: int = 6) -> list[Student]:
    """Подбираем по одному студенту на каждый «вид носителя», чтобы глазами
    увидеть расписание от лица разных студентов."""
    selected: list[Student] = []
    seen_kinds: set[str] = set()

    def add(student: Student | None, kind: str):
        if student is not None and kind not in seen_kinds and len(selected) < limit:
            seen_kinds.add(kind)
            selected.append(student)

    events = LessonEvent.objects.filter(semester=run.semester)

    # 1. Обычная группа (практика/лаборатория).
    ev = events.filter(lesson_type=LessonType.LAB, group__is_dpo=False,
                       group__students__isnull=False).first()
    if ev is None:
        ev = events.filter(lesson_type=LessonType.PRACTICE, group__is_dpo=False,
                           group__students__isnull=False).first()
    if ev is not None:
        add(ev.group.students.first(), "обычная группа")

    # 2. Группа, входящая в лекционный поток.
    stream = LectureStream.objects.filter(semester=run.semester,
                                          groups__students__isnull=False).first()
    if stream is not None:
        for g in stream.groups.filter(students__isnull=False).distinct():
            add(g.students.first(), "лекционный поток")
            break

    # 3. Студент с выбранной ПД.
    enrollment = (ProjectEnrollment.objects
                  .filter(project__events__semester=run.semester,
                          project__events__lesson_type=LessonType.PD)
                  .select_related("student").first())
    if enrollment is not None:
        add(enrollment.student, "проектная деятельность")

    # 4. Слушатель группы ДПО.
    dpo_event = events.filter(lesson_type=LessonType.DPO,
                              group__students__isnull=False).first()
    if dpo_event is not None:
        add(dpo_event.group.students.first(), "ДПО")

    # 5. Внеучебка.
    extra_event = events.filter(lesson_type=LessonType.EXTRACURRICULAR,
                                group__students__isnull=False).first()
    if extra_event is not None:
        add(extra_event.group.students.first(), "внеучебка")

    # 6. Если набрано мало — любые студенты с группой этого семестра.
    if len(selected) < limit:
        chosen_ids = {s.pk for s in selected}
        fallback = (Student.objects
                    .filter(group__semester=run.semester)
                    .exclude(pk__in=chosen_ids)
                    .select_related("group")[:limit - len(selected)])
        selected.extend(fallback)

    return selected


+++ dev_dashboard/student_view.py (修改后)
"""Представление расписания «глазами студента» для dev-панели.

Расписание собирается из результата последнего GenerationRun:
- практика/лаборатория/внеучебка/ДПО — пары носителя (группы), в котором
  числится студент;
- лекция — пары потока, куда входит группа студента;
- ПД — пары проекта, который студент выбрал (ProjectEnrollment);
- физкультура (PE) не генерируется и показывается отдельной пометкой.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Q

from schedule_generator.enums import LessonType, NON_GENERATED_LESSON_TYPES, effective_format
from schedule_generator.models import (
    GenerationRun,
    LectureStream,
    LessonEvent,
    ProjectEnrollment,
    ScheduledClass,
    Student,
    TimeSlot,
)

DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]


def get_latest_run() -> GenerationRun | None:
    return GenerationRun.objects.order_by("-created_at").first()


def _build_lesson_map(run: GenerationRun, group_ids: set[int],
                      enrollment_project_ids: set[int]) -> dict[int, list[dict]]:
    """slot_id -> список занятий, релевантных носителю студента.

    Носители: учебная группа студента (включая группу ДПО), её лекционные
    потоки и выбранные студентом проекты ПД.
    """
    stream_ids = list(LectureStream.objects.filter(groups__in=group_ids)
                      .values_list("id", flat=True))
    holder_filter = (
        Q(event__group_id__in=group_ids)
        | Q(event__stream_id__in=stream_ids)
        | Q(event__project_id__in=enrollment_project_ids)
    )
    classes = (ScheduledClass.objects
               .filter(run=run)
               .filter(holder_filter)
               .exclude(event__lesson_type__in=NON_GENERATED_LESSON_TYPES)
               .select_related("slot", "room", "event__subject",
                               "event__teacher", "event__group", "event__stream"))
    by_slot: dict[int, list[dict]] = {}
    for sc in classes:
        event = sc.event
        fmt = effective_format(event.lesson_type, event.format)
        by_slot.setdefault(sc.slot_id, []).append({
            "subject": event.subject.name,
            "lesson_type_display": event.get_lesson_type_display(),
            "teacher": event.teacher.full_name if event.teacher_id else "—",
            "room": sc.room.name if sc.room_id else ("онлайн" if fmt == "ONLINE" else "—"),
            "is_online": fmt == "ONLINE",
            "highlight": event.lesson_type == LessonType.PD,
        })
    return by_slot


@dataclass
class StudentSchedule:
    student: Student
    rows: list[list[list[dict]]] = field(default_factory=list)  # [номер пары][день]
    num_days: int = 0
    total_pairs: int = 0
    pe_note: str = ""

    @property
    def day_names(self) -> list[str]:
        """Названия дней для колонок таблицы (пн–сб)."""
        return DAY_NAMES[:self.num_days]

    @property
    def grid(self) -> list[dict]:
        """Сетка для шаблона: строки (номера пар) с ячейками по дням недели.

        Каждая строка — {'lesson': номер_пары, 'cells': [ячейка_дня, ...]},
        где ячейка — {'lessons': [...]} либо None (окно/нет слота).
        """
        out = []
        for r, row in enumerate(self.rows):
            out.append({
                "lesson": r + 1,
                "cells": [{"lessons": cell} if cell else None for cell in row],
            })
        return out


def build_student_schedule(run: GenerationRun, student: Student) -> StudentSchedule:
    group_ids = {student.group_id}
    enrollment = ProjectEnrollment.objects.filter(student=student).first()
    project_ids = {enrollment.project_id} if enrollment else set()

    lesson_map = _build_lesson_map(run, group_ids, project_ids)

    slots = list(TimeSlot.objects
                 .filter(week_pattern__semester=run.semester)
                 .order_by("day_of_week", "lesson_number"))
    max_day = max((s.day_of_week for s in slots), default=0)
    max_lesson = max((s.lesson_number for s in slots), default=0)
    # days[день][номер пары] — список занятий в ячейке.
    days: list[list[list[dict]]] = [[[] for _ in range(max_lesson)]
                                    for _ in range(max_day + 1)]
    total = 0
    for slot in slots:
        lessons = lesson_map.get(slot.id)
        if not lessons:
            continue
        days[slot.day_of_week][slot.lesson_number - 1].extend(lessons)
        total += len(lessons)

    pe_events = (LessonEvent.objects
                 .filter(semester=run.semester, group_id__in=group_ids,
                         lesson_type=LessonType.PE)
                 .select_related("subject"))
    pe_note = ""
    if pe_events.exists():
        names = ", ".join(e.subject.name for e in pe_events)
        pe_note = (f"Физкультура ({names}) в расписании не участвует: "
                   f"{sum(e.pairs_per_week for e in pe_events)} пар/нед — свободно.")

    # Транспонируем: строки таблицы — номера пар, колонки — дни недели.
    rows = [[days[d][l] for d in range(max_day + 1)] for l in range(max_lesson)]
    return StudentSchedule(student=student, rows=rows, num_days=max_day + 1,
                           total_pairs=total, pe_note=pe_note)


def pick_representative_students(run: GenerationRun, limit: int = 6) -> list[Student]:
    """Подбираем по одному студенту на каждый «вид носителя», чтобы глазами
    увидеть расписание от лица разных студентов."""
    selected: list[Student] = []
    seen_kinds: set[str] = set()

    def add(student: Student | None, kind: str):
        if student is not None and kind not in seen_kinds and len(selected) < limit:
            seen_kinds.add(kind)
            selected.append(student)

    events = LessonEvent.objects.filter(semester=run.semester)

    # 1. Обычная группа (практика/лаборатория).
    ev = events.filter(lesson_type=LessonType.LAB, group__is_dpo=False,
                       group__students__isnull=False).first()
    if ev is None:
        ev = events.filter(lesson_type=LessonType.PRACTICE, group__is_dpo=False,
                           group__students__isnull=False).first()
    if ev is not None:
        add(ev.group.students.first(), "обычная группа")

    # 2. Группа, входящая в лекционный поток.
    stream = LectureStream.objects.filter(semester=run.semester,
                                          groups__students__isnull=False).first()
    if stream is not None:
        for g in stream.groups.filter(students__isnull=False).distinct():
            add(g.students.first(), "лекционный поток")
            break

    # 3. Студент с выбранной ПД.
    enrollment = (ProjectEnrollment.objects
                  .filter(project__events__semester=run.semester,
                          project__events__lesson_type=LessonType.PD)
                  .select_related("student").first())
    if enrollment is not None:
        add(enrollment.student, "проектная деятельность")

    # 4. Слушатель группы ДПО.
    dpo_event = events.filter(lesson_type=LessonType.DPO,
                              group__students__isnull=False).first()
    if dpo_event is not None:
        add(dpo_event.group.students.first(), "ДПО")

    # 5. Внеучебка.
    extra_event = events.filter(lesson_type=LessonType.EXTRACURRICULAR,
                                group__students__isnull=False).first()
    if extra_event is not None:
        add(extra_event.group.students.first(), "внеучебка")

    # 6. Если набрано мало — любые студенты с группой этого семестра.
    if len(selected) < limit:
        chosen_ids = {s.pk for s in selected}
        fallback = (Student.objects
                    .filter(group__semester=run.semester)
                    .exclude(pk__in=chosen_ids)
                    .select_related("group")[:limit - len(selected)])
        selected.extend(fallback)

    return selected
