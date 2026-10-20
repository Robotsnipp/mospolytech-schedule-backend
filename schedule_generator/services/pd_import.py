"""Импорт проверенной выгрузки ПД с явным сопоставлением ID приложения."""

import re
from datetime import date, time

from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import FixedProjectSession, Project, Room, Teacher


class PDImportError(ValueError):
    pass


def positive_id(value):
    if type(value) is not int or value <= 0:
        raise PDImportError("ID и номер проекта должны быть положительными целыми числами.")
    return value


def parse_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise PDImportError("У каждого интервала нужны точные start/end в формате HH:MM.")
    return time.fromisoformat(value)


@transaction.atomic
def import_fixed_pd(*, document, mapping, semester, travel_buffer_minutes, module=None):
    """Обновляет только выбранные проекты/модуль, сохраняя ручные записи.

    Неподдержанные календарные примечания не пропускаются молча: такой проект
    нужно сначала разобрать вручную. Датированные интервалы поддержаны как
    консервативные ограничения недельного шаблона.
    """
    if not isinstance(document, dict) or not isinstance(mapping, dict):
        raise PDImportError("Выгрузка и файл сопоставлений должны быть JSON-объектами.")
    if document.get("kind") != "pd" or document.get("schema_version") != 1 or document.get("parser_revision", 0) < 4:
        raise PDImportError("Нужна выгрузка ПД актуального парсера (parser_revision >= 4).")
    source = document.get("source", {})
    digest = source.get("sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or mapping.get("source_sha256") != digest:
        raise PDImportError("SHA-256 в файле сопоставлений не совпадает с источником выгрузки.")
    if mapping.get("reviewed") is not True:
        raise PDImportError("Файл сопоставлений должен подтверждать проверку выбранных записей: reviewed=true.")
    if type(travel_buffer_minutes) is not int or not 0 <= travel_buffer_minutes <= 240:
        raise PDImportError("Запас на дорогу нужно явно задать целым числом от 0 до 240 минут.")
    if module and module.semester_id != semester.pk:
        raise PDImportError("Модуль относится к другому семестру.")
    year = re.fullmatch(r"(20\d{2})\s*/\s*(20\d{2})", document.get("academic_year") or "")
    if not year or not date(int(year[1]), 9, 1) <= semester.start_date < date(int(year[2]), 9, 1):
        raise PDImportError("Учебный год источника не соответствует началу семестра.")
    rows = document.get("projects", [])
    by_number = {p["source_number"]: p for p in rows}
    if len(rows) != len(by_number):
        raise PDImportError("В выгрузке повторяются номера проектов.")
    selections = mapping.get("projects", [])
    if not selections:
        raise PDImportError("Нужно явно сопоставить хотя бы один проект.")
    numbers = [positive_id(p["source_number"]) for p in selections]
    project_ids = [positive_id(p["project_id"]) for p in selections]
    if len(set(numbers)) != len(numbers) or len(set(project_ids)) != len(project_ids):
        raise PDImportError("Проекты в сопоставлении должны быть однозначными.")
    projects = {p.pk: p for p in Project.objects.select_for_update().filter(pk__in=project_ids)}
    pending = []
    for selection, number, project_id in zip(selections, numbers, project_ids, strict=True):
        row, project = by_number.get(number), projects.get(project_id)
        if row is None or project is None or project.semester_id != semester.pk:
            raise PDImportError(f"Проект №{number}: нет строки источника или проекта в выбранном семестре.")
        if not project.enrollments.exists():
            raise PDImportError(f"Проект №{number}: сначала нужны связи студент → проект.")
        teacher_id = selection.get("teacher_id", project.supervisor_id)
        if teacher_id is None or not Teacher.objects.filter(pk=positive_id(teacher_id)).exists():
            raise PDImportError(f"Проект №{number}: нужен сопоставленный преподаватель.")
        count = 0
        for schedule_index, schedule in enumerate(row.get("schedules", [])):
            if schedule.get("notes") or schedule.get("changes"):
                raise PDImportError(f"Проект №{number}: есть календарные примечания/переносы без готового интервала; "
                                    "сначала разберите их вручную.")
            for index, meeting in enumerate(schedule.get("meetings", [])):
                if meeting.get("teacher_override_raw"):
                    raise PDImportError(f"Проект №{number}: замена преподавателя требует отдельной ручной записи.")
                recurrence = meeting.get("recurrence")
                if recurrence not in ("dated", "weekly_candidate"):
                    raise PDImportError(f"Проект №{number}: неизвестное правило повторения.")
                meeting_date = date.fromisoformat(meeting["date"]) if recurrence == "dated" else None
                place = meeting.get("place_details") or {}
                if place.get("subgroup_number") is not None:
                    raise PDImportError(f"Проект №{number}: для подгрупп нужен отдельный состав участников.")
                raw = place.get("raw")
                if not raw:
                    raise PDImportError(f"Проект №{number}: не определено место проведения.")
                room_id = selection.get("rooms", {}).get(raw)
                if (place.get("venue") or {}).get("kind") == "additional":
                    if room_id is not None or place.get("resource_policy", {}).get("reserve_room") is not False:
                        raise PDImportError(f"Проект №{number}: дополнительная площадка не резервирует комнату.")
                elif place.get("kind") in ("rooms", "address_with_rooms", "university_address"):
                    if room_id is None or len(place.get("room_candidates", [])) > 1:
                        raise PDImportError(f"Проект №{number}: нужно однозначное сопоставление помещения {raw!r}.")
                    positive_id(room_id)
                    if not Room.objects.filter(pk=room_id, is_active=True).exists():
                        raise PDImportError(f"Проект №{number}: аудитория отсутствует или недоступна.")
                else:
                    raise PDImportError(f"Проект №{number}: место не классифицировано актуальным парсером.")
                cell = schedule["source_cell"]
                key = f"pd:{module.pk if module else 'common'}:{number}:{cell['a1']}:{schedule_index}:{index}"
                session = FixedProjectSession(
                    project=project, module=module, teacher_id=teacher_id,
                    day_of_week=meeting["day_of_week"], start_time=parse_time(meeting.get("start")),
                    end_time=parse_time(meeting.get("end")), date=meeting_date, room_id=room_id,
                    place_details=place, travel_buffer_minutes=travel_buffer_minutes, source_key=key,
                    source={"provider": "google_pd_sheet", "sha256": digest, "url": source.get("url"),
                            "source_number": number, "cell": cell, "schedule_index": schedule_index,
                            "meeting_index": index,
                            "teacher_raw": row.get("teacher_raw"), "recurrence": recurrence},
                )
                try:
                    session.full_clean(validate_unique=False, validate_constraints=False)
                except ValidationError as exc:
                    raise PDImportError(f"Проект №{number}: {exc}") from exc
                pending.append(session)
                count += 1
        if count == 0:
            raise PDImportError(f"Проект №{number}: нет интервалов для импорта.")
    # Валидация всего выбранного набора предшествует изменениям БД.
    created = updated = 0
    for session in pending:
        existing = FixedProjectSession.objects.filter(project_id=session.project_id,
                                                       source_key=session.source_key).first()
        if existing and existing.source.get("provider") != "google_pd_sheet":
            raise PDImportError(f"Ключ {session.source_key!r} занят ручной записью; импорт отменён.")
        values = {field: getattr(session, field) for field in (
            "module", "teacher_id", "day_of_week", "start_time", "end_time", "date", "room_id",
            "place_details", "travel_buffer_minutes", "source")}
        _, was_created = FixedProjectSession.objects.update_or_create(
            project_id=session.project_id, source_key=session.source_key, defaults=values)
        created += was_created
        updated += not was_created
    keys = [s.source_key for s in pending]
    removed, _ = FixedProjectSession.objects.filter(
        project_id__in=project_ids, module=module, source__provider="google_pd_sheet"
    ).exclude(source_key__in=keys).delete()
    return {"created": created, "updated": updated, "removed": removed,
            "mapped_projects": len(project_ids), "unmapped_projects": len(rows) - len(numbers)}
