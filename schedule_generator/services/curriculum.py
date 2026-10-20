"""Проверенная выгрузка плана -> строки БД -> нагрузка группы в модуле."""

import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from ..enums import LessonType
from ..models import (
    Curriculum,
    CurriculumEntry,
    Group,
    LectureStream,
    LessonEvent,
    Subject,
    Teacher,
)

HOURS = ("lecture_hours", "practice_hours", "lab_hours")
CONTROLS = {
    "has_exam": "exam_semesters",
    "has_credit": "credit_semesters",
    "has_graded_credit": "graded_credit_semesters",
}
TYPE_HOURS = {
    LessonType.LECTURE: "lecture_hours",
    LessonType.PRACTICE: "practice_hours",
    LessonType.LAB: "lab_hours",
}


class CurriculumDataError(ValueError):
    """Неполные исходные данные или несовместимое распределение часов."""


def integer(value, label, minimum=1):
    if type(value) is not int or value < minimum:
        raise CurriculumDataError(f"{label}: нужно целое число не меньше {minimum}.")
    return value


def object_value(value, label):
    if not isinstance(value, dict):
        raise CurriculumDataError(f"{label}: нужен JSON-объект.")
    return value


def text_value(value, label):
    if not isinstance(value, str) or not value.strip():
        raise CurriculumDataError(f"{label}: нужна непустая строка.")
    return value.strip()


def save_validated(instance):
    try:
        instance.full_clean()
    except ValidationError as exc:
        raise CurriculumDataError(str(exc)) from exc
    instance.save()


def validate_hours(entry):
    for name in (*HOURS, "contact_hours", "independent_hours"):
        value = getattr(entry, name)
        if value is not None:
            integer(value, name, 0)
    for name in CONTROLS:
        if getattr(entry, name) is not None and type(getattr(entry, name)) is not bool:
            raise CurriculumDataError(f"{name}: нужно true, false или null.")
    parts = [getattr(entry, name) for name in HOURS]
    known_hours = sum(value for value in parts if value is not None)
    if entry.contact_hours is not None and (
        known_hours > entry.contact_hours
        or all(value is not None for value in parts)
        and known_hours != entry.contact_hours
    ):
        raise CurriculumDataError(
            f"{entry.discipline_code}: сумма часов по видам не равна аудиторным часам."
        )


def events_for_group(entry, group_id):
    return (
        LessonEvent.objects.filter(curriculum_entry=entry)
        .filter(
            Q(group_id=group_id)
            | Q(stream__groups__pk=group_id)
            | Q(prepared_for_group_id=group_id)
        )
        .distinct()
    )


def validate_budget(entry):
    """Бюджет семестровых часов проверяется отдельно для каждой учебной группы."""
    events = LessonEvent.objects.filter(
        curriculum_entry=entry, lesson_type__in=TYPE_HOURS
    ).select_related("group", "stream")
    group_ids = set()
    for event in events:
        group_ids.update(
            [event.group_id]
            if event.group_id
            else event.stream.groups.values_list("pk", flat=True)
        )
    for group_id in group_ids:
        totals = dict.fromkeys(TYPE_HOURS, 0)
        for event in events_for_group(entry, group_id):
            if event.lesson_type not in totals:
                continue
            if event.academic_hours is None:
                raise CurriculumDataError(
                    f"{entry}: у существующего занятия не заданы академические часы модуля."
                )
            totals[event.lesson_type] += event.academic_hours
        for lesson_type, allocated in totals.items():
            available = getattr(entry, TYPE_HOURS[lesson_type])
            if allocated and (available is None or allocated > available):
                raise CurriculumDataError(
                    f"{entry}: распределено {allocated} ч {lesson_type}, в плане {available}."
                )


@transaction.atomic
def import_curriculum(*, document, review):
    """Импортирует только явно выбранные строки/семестры, сохраняя неизвестные значения."""
    document, review = (
        object_value(document, "document"),
        object_value(review, "review"),
    )
    if document.get("schema_version") != 1:
        raise CurriculumDataError("Нужна выгрузка учебного плана schema_version=1.")
    source = object_value(document.get("source"), "source")
    digest = source.get("sha256")
    if (
        not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or review.get("source_sha256") != digest
        or review.get("reviewed") is not True
    ):
        raise CurriculumDataError(
            "Подтвердите reviewed=true и source_sha256 выбранной выгрузки."
        )
    metadata = object_value(document.get("metadata"), "metadata")
    forms = {"очная": "FULL_TIME", "очно-заочная": "PART_TIME", "заочная": "EXTRAMURAL"}
    form = forms.get(
        text_value(metadata.get("study_form_raw"), "Форма обучения").casefold()
    )
    if form is None:
        raise CurriculumDataError("Неизвестная форма обучения.")
    identity = {
        "program_code": text_value(metadata.get("program_code"), "Код программы"),
        "profile": text_value(metadata.get("profile_name"), "Профиль"),
        "admission_year": integer(metadata.get("admission_year"), "Год набора"),
        "study_form": form,
        "version": text_value(review.get("version", "1"), "Версия"),
    }
    selections = review.get("entries")
    if not isinstance(selections, list) or not selections:
        raise CurriculumDataError("entries: выберите строки и семестры для импорта.")
    raw_rows = document.get("entries")
    if not isinstance(raw_rows, list):
        raise CurriculumDataError("entries выгрузки: нужен список.")
    rows = {}
    for row in raw_rows:
        object_value(row, "Строка плана")
        number = integer(row.get("source_row"), "source_row", 0)
        if number in rows:
            raise CurriculumDataError("Повторяется source_row в выгрузке.")
        rows[number] = row
    plan = Curriculum.objects.select_for_update().filter(**identity).first()
    if plan is not None and plan.source.get("provider") != "curriculum_parser":
        raise CurriculumDataError(
            "План с такой версией создан вручную; выберите отдельную версию импорта."
        )
    plan_created = plan is None
    plan = plan or Curriculum(**identity)
    plan.name = text_value(review.get("name", identity["profile"]), "Название плана")
    plan.duration_months = integer(review.get("duration_months"), "duration_months")
    plan.source = {**source, "provider": "curriculum_parser"}
    save_validated(plan)
    if plan.entries.filter(semester_number__gt=plan.duration_months // 6).exists():
        raise CurriculumDataError(
            "Срок обучения меньше семестра уже сохранённой строки плана."
        )
    selected, keys, entry_ids = set(), set(), []
    created = updated = 0
    for selection in selections:
        object_value(selection, "Выбранная строка")
        number = integer(selection.get("source_row"), "source_row", 0)
        semester = integer(selection.get("semester_number"), "semester_number")
        if (number, semester) in selected:
            raise CurriculumDataError("Выбранная строка и семестр повторяются.")
        selected.add((number, semester))
        row = rows.get(number)
        if row is None:
            raise CurriculumDataError(f"В выгрузке нет source_row={number}.")
        code = text_value(row.get("discipline_code"), f"Код строки {number}")
        if (code, semester) in keys or semester * 6 > plan.duration_months:
            raise CurriculumDataError(
                "Повторяется код/семестр или семестр выходит за срок обучения."
            )
        keys.add((code, semester))
        semester_loads = row.get("semester_loads")
        if not isinstance(semester_loads, list):
            raise CurriculumDataError("semester_loads: нужен список.")
        for item in semester_loads:
            object_value(item, "Семестровая нагрузка")
        loads = [
            item for item in semester_loads if item.get("semester_number") == semester
        ]
        if len(loads) != 1:
            raise CurriculumDataError(
                f"Строка {number}: нет однозначной нагрузки семестра {semester}."
            )
        activity = selection.get("activity_kind")
        if activity not in ("REGULAR", "PD", "PE"):
            raise CurriculumDataError(
                "Для строки задайте activity_kind: REGULAR, PD или PE."
            )
        name = text_value(row.get("subject"), "Название дисциплины")
        if selection.get("subject_id") is not None:
            subject = Subject.objects.filter(
                pk=integer(selection["subject_id"], "subject_id")
            ).first()
            if subject is None or subject.name != name:
                raise CurriculumDataError(
                    "subject_id отсутствует или название отличается от источника."
                )
        else:
            matches = list(Subject.objects.filter(name=name)[:2])
            if len(matches) > 1:
                raise CurriculumDataError(
                    "Есть одноимённые дисциплины: укажите subject_id."
                )
            subject = matches[0] if matches else Subject(name=name)
            if not subject.pk:
                save_validated(subject)
        entry = CurriculumEntry.objects.filter(
            curriculum=plan, discipline_code=code, semester_number=semester
        ).first()
        previous_row = CurriculumEntry.objects.filter(
            curriculum=plan,
            semester_number=semester,
            source__provider="curriculum_parser",
            source__source_row=number,
        ).first()
        if entry and previous_row and entry.pk != previous_row.pk:
            raise CurriculumDataError(
                "Исправленный код пересекается с другой строкой плана."
            )
        entry = entry or previous_row
        if entry and entry.source.get("provider") != "curriculum_parser":
            raise CurriculumDataError(
                "Строка плана создана вручную; импорт её не заменяет."
            )
        is_new = entry is None
        entry = entry or CurriculumEntry(curriculum=plan, semester_number=semester)
        if entry.pk and entry.subject_id != subject.pk and entry.events.exists():
            raise CurriculumDataError(
                "У строки уже есть занятия; замена дисциплины требует новой версии плана."
            )
        if (
            entry.pk
            and entry.source.get("activity_kind") != activity
            and entry.events.exists()
        ):
            raise CurriculumDataError(
                "У строки уже есть занятия; изменение REGULAR/PD/PE требует новой версии плана."
            )
        entry.subject, entry.discipline_code = subject, code
        for field in (*HOURS, "contact_hours"):
            setattr(entry, field, loads[0].get(field))
        totals = object_value(row.get("totals", {}), "totals")
        controls = object_value(row.get("controls", {}), "controls")
        entry.independent_hours = (
            totals.get("independent_hours") if len(semester_loads) == 1 else None
        )
        for field, control in CONTROLS.items():
            terms = controls.get(control)
            if terms is not None and (
                not isinstance(terms, list) or any(type(t) is not int for t in terms)
            ):
                raise CurriculumDataError(
                    f"{control}: ожидается список семестров или null."
                )
            setattr(entry, field, None if terms is None else semester in terms)
        overrides = object_value(selection.get("overrides", {}), "overrides")
        if set(overrides) - {*HOURS, "contact_hours", "independent_hours", *CONTROLS}:
            raise CurriculumDataError("Неизвестное поле в overrides.")
        for field, value in overrides.items():
            setattr(entry, field, value)
        entry.source = {
            "provider": "curriculum_parser",
            "sha256": digest,
            "reviewed": True,
            "source_row": number,
            "activity_kind": activity,
            "overrides": overrides,
            "raw_cells": row.get("raw_cells", {}),
            "warnings": row.get("warnings", []),
            "controls": controls,
            "semester_load": loads[0],
            "totals": row.get("totals", {}),
            "source_bbox_pixels": row.get("source_bbox_pixels"),
            "is_elective_candidate": row.get("is_elective_candidate", False),
        }
        validate_hours(entry)
        save_validated(entry)
        validate_budget(entry)
        entry_ids.append(entry.pk)
        created += is_new
        updated += not is_new
    return {
        "curriculum_id": plan.pk,
        "curriculum_created": plan_created,
        "created": created,
        "updated": updated,
        "entry_ids": entry_ids,
        "unselected_source_rows": len(rows) - len({s[0] for s in selected}),
    }


@transaction.atomic
def prepare_module_load(
    *, group, module, semester_number, entries, academic_hours_per_pair=2, replace=False
):
    """Часы модуля -> целое число пар в неделю; округления и назначения по имени отсутствуют."""
    group = Group.objects.select_for_update().get(pk=group.pk)
    if not group.curriculum_id or group.is_dpo:
        raise CurriculumDataError("Нужна учебная группа с привязанным Curriculum.")
    plan = Curriculum.objects.select_for_update().get(pk=group.curriculum_id)
    if group.admission_year is not None and group.admission_year != plan.admission_year:
        raise CurriculumDataError("Год набора группы отличается от учебного плана.")
    if group.semester_id is not None and group.semester_id != module.semester_id:
        raise CurriculumDataError("Семестр группы отличается от семестра модуля.")
    integer(semester_number, "semester_number")
    integer(academic_hours_per_pair, "academic_hours_per_pair")
    if type(replace) is not bool or not isinstance(entries, list) or not entries:
        raise CurriculumDataError("Нужен непустой список entries и логический replace.")
    size = group.students.count()
    if not size:
        raise CurriculumDataError(
            "У группы нет студентов; размер аудитории неизвестен."
        )
    module.full_clean()
    existing = list(
        LessonEvent.objects.filter(
            prepared_for_group=group,
            module=module,
            curriculum_entry__semester_number=semester_number,
        ).select_related("stream")
    )
    by_key = {
        (event.curriculum_entry_id, event.lesson_type): event for event in existing
    }
    selected, touched, seen, created, updated = [], set(), set(), 0, 0
    for row in entries:
        object_value(row, "Нагрузка")
        entry = CurriculumEntry.objects.filter(
            pk=integer(row.get("curriculum_entry_id"), "curriculum_entry_id"),
            curriculum=plan,
            semester_number=semester_number,
        ).first()
        lesson_type = row.get("lesson_type")
        if entry is None or lesson_type not in TYPE_HOURS:
            raise CurriculumDataError(
                "Нужна строка выбранного плана/семестра и тип LECTURE, PRACTICE или LAB."
            )
        if entry.source and (
            entry.source.get("reviewed") is not True
            or entry.source.get("activity_kind") != "REGULAR"
        ):
            raise CurriculumDataError(
                "ПД/физкультура и непроверенные строки не создают обычные пары группы."
            )
        hours = integer(row.get("academic_hours"), "academic_hours")
        denominator = academic_hours_per_pair * module.teaching_weeks
        pairs, remainder = divmod(hours, denominator)
        if remainder:
            raise CurriculumDataError(
                f"{entry}: {hours} ч не делятся на {denominator}; недельный шаблон не умеет остаточные пары."
            )
        teacher = Teacher.objects.filter(
            pk=integer(row.get("teacher_id"), "teacher_id")
        ).first()
        if teacher is None:
            raise CurriculumDataError("Преподаватель не найден.")
        key = (entry.pk, lesson_type)
        if key in seen:
            raise CurriculumDataError("Повторяется строка плана и тип занятия.")
        seen.add(key)
        event = by_key.get(key)
        conflicts = (
            LessonEvent.objects.filter(
                semester=module.semester, subject=entry.subject, lesson_type=lesson_type
            )
            .filter(Q(module=module) | Q(module__isnull=True))
            .filter(Q(group=group) | Q(stream__groups=group))
            .distinct()
        )
        if conflicts.exclude(pk=event.pk if event else None).exists():
            raise CurriculumDataError(
                "У группы уже есть соответствующее занятие, созданное вне этой подготовки."
            )
        source = {
            "provider": "module_load",
            "academic_hours_per_pair": academic_hours_per_pair,
            "teaching_weeks": module.teaching_weeks,
            "curriculum_source": entry.source,
        }
        is_new = event is None
        event = event or LessonEvent(
            semester=module.semester,
            module=module,
            curriculum_entry=entry,
            prepared_for_group=group,
            lesson_type=lesson_type,
        )
        changed = (
            event.teacher_id != teacher.pk
            or event.pairs_per_week != pairs
            or event.academic_hours != hours
            or event.audience_size != size
            or event.preparation_source != source
        )
        if not is_new and changed and event.classes.exists():
            raise CurriculumDataError(
                "Занятие уже использовано в расписании; измените версию/период, сохранив историю."
            )
        event.subject, event.teacher, event.pairs_per_week = (
            entry.subject,
            teacher,
            pairs,
        )
        event.academic_hours, event.audience_size, event.preparation_source = (
            hours,
            size,
            source,
        )
        if lesson_type == LessonType.LECTURE:
            if event.stream_id:
                if set(event.stream.groups.values_list("pk", flat=True)) != {group.pk}:
                    raise CurriculumDataError(
                        "Поток объединён с другими группами; автоматическое изменение запрещено."
                    )
                stream = event.stream
                if (
                    stream.teacher_id != teacher.pk
                    and stream.events.exclude(pk=event.pk).exists()
                ):
                    raise CurriculumDataError(
                        "Поток используется другими занятиями; назначьте отдельный поток."
                    )
                stream.teacher = teacher
                stream.save(update_fields=["teacher"])
            else:
                stream = LectureStream.objects.create(
                    semester=module.semester, subject=entry.subject, teacher=teacher
                )
                stream.groups.add(group)
            event.stream, event.group = stream, None
        else:
            event.group = group
        save_validated(event)
        selected.append(event.pk)
        touched.add(entry.pk)
        created += is_new
        updated += not is_new
    removed = 0
    if replace:
        for stale in existing:
            if stale.pk not in selected:
                if stale.classes.exists():
                    raise CurriculumDataError(
                        "Устаревшее занятие уже использовано; сохраните историю расписания."
                    )
                touched.add(stale.curriculum_entry_id)
                stream = stale.stream
                stale.delete()
                if stream and not stream.events.exists():
                    stream.delete()
                removed += 1
    for entry in CurriculumEntry.objects.filter(pk__in=touched):
        validate_budget(entry)
    return {
        "group_id": group.pk,
        "module_id": module.pk,
        "created": created,
        "updated": updated,
        "removed": removed,
        "event_ids": selected,
        "academic_hours": sum(row["academic_hours"] for row in entries),
    }


def validate_prepared_load(events):
    """Изменение календаря после подготовки требует повторного расчёта нагрузки."""
    for event in events.filter(prepared_for_group__isnull=False).select_related(
        "module"
    ):
        source = event.preparation_source
        hours_per_pair = source.get("academic_hours_per_pair")
        if (
            not event.module_id
            or type(hours_per_pair) is not int
            or hours_per_pair < 1
            or source.get("teaching_weeks") != event.module.teaching_weeks
            or event.academic_hours
            != event.pairs_per_week * event.module.teaching_weeks * hours_per_pair
        ):
            raise CurriculumDataError(
                f"Занятие {event.pk}: календарь или часы изменены; повторите prepare-load."
            )
