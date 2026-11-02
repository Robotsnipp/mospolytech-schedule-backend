"""Copy, validate and persist manual alternatives without invoking the solver."""

from copy import deepcopy

from django.db import transaction
from django.http import Http404

from ..models import GenerationRun, ManualPlacement, ManualScheduleVariant
from .placement_validation import validate_placements
from .resource_profiles import room_nearness_key


class ManualVariantError(Exception):
    def __init__(self, message, validation=None):
        super().__init__(message)
        self.validation = validation


def _metadata(variant):
    return {"id": variant.pk, "name": variant.name,
            "created_at": variant.created_at.isoformat(),
            "source_run_id": variant.source_run_id,
            "parent_variant_id": variant.parent_variant_id,
            "validation": variant.validation}


def list_variants(run_id):
    if not GenerationRun.objects.filter(pk=run_id).exists():
        raise Http404("Исходный запуск не найден.")
    return [_metadata(variant) for variant in ManualScheduleVariant.objects.filter(source_run_id=run_id)]


def read_variant(variant_id):
    try:
        variant = ManualScheduleVariant.objects.get(pk=variant_id)
    except ManualScheduleVariant.DoesNotExist as exc:
        raise Http404("Ручной вариант не найден.") from exc
    result = deepcopy(variant.calendar_snapshot)
    result["variant"] = _metadata(variant)
    result["validation"] = variant.validation
    result["placements"] = [
        {"class_id": item.source_class_id, "event_id": item.event_id,
         "slot_id": item.slot_id, "room_id": item.room_id}
        for item in variant.placements.all()]
    return result


def _load_source(run_id, variant_id=None, lock=False):
    queryset = GenerationRun.objects.select_for_update() if lock else GenerationRun.objects
    try:
        run = queryset.get(pk=run_id)
    except GenerationRun.DoesNotExist as exc:
        raise Http404("Исходный запуск не найден.") from exc
    snapshot = run.input_snapshot
    if not run.settings_snapshot or not snapshot or snapshot.get("schema_version") != 1:
        raise ManualVariantError("У исторического запуска нет полного снимка входов. Для ручных правок выполните новый расчёт.")
    if run.status not in ("SOLVED", "PARTIAL"):
        raise ManualVariantError("Для ручного варианта нужен сохранённый результат с размещёнными занятиями.")
    parent = None
    if variant_id is not None:
        try:
            parent = ManualScheduleVariant.objects.get(pk=variant_id, source_run=run)
        except ManualScheduleVariant.DoesNotExist as exc:
            raise Http404("Вариант не найден у выбранного запуска.") from exc
        placements = [{"class_id": item.source_class_id, "event_id": item.event_id,
                       "slot_id": item.slot_id, "room_id": item.room_id}
                      for item in parent.placements.all()]
    else:
        placements = [{"class_id": item.pk, "event_id": item.event_id,
                       "slot_id": item.slot_id, "room_id": item.room_id}
                      for item in run.scheduled_classes.order_by("pk")]
    if not placements:
        raise ManualVariantError("В исходном результате нет размещённых пар.")
    return run, parent, placements


def _apply_changes(snapshot, placements, changes):
    result = deepcopy(placements)
    by_class = {item["class_id"]: item for item in result}
    slots = {item["id"]: item for item in snapshot["slots"]}
    rooms = {item["id"]: item for item in snapshot["rooms"]}
    seen = set()
    for change in changes:
        class_id = change["class_id"]
        if class_id in seen:
            raise ManualVariantError("Для одной пары передано несколько изменений.")
        seen.add(class_id)
        placement = by_class.get(class_id)
        if placement is None:
            raise ManualVariantError("Изменяемая пара не принадлежит исходному расписанию.")
        if "slot_id" in change:
            target = slots.get(change["slot_id"])
            if target is None:
                raise ManualVariantError("Выбранный слот отсутствует в снимке исходного запуска.")
            original = slots[placement["slot_id"]]
            if target["end_minute"] - target["start_minute"] != original["end_minute"] - original["start_minute"]:
                raise ManualVariantError("Перенос не может менять продолжительность пары.")
            placement["slot_id"] = change["slot_id"]
        if "room_id" in change:
            if change["room_id"] is not None and change["room_id"] not in rooms:
                raise ManualVariantError("Выбранная аудитория отсутствует в ресурсном пуле исходного запуска.")
            placement["room_id"] = change["room_id"]
    return result


def _calendar_rows(snapshot, placements, run_id):
    slots = {row["id"]: row for row in snapshot["slots"]}
    events = {row.get("event_id", row.get("id")): row for row in snapshot["events"]}
    rooms = {row["id"]: row for row in snapshot["rooms"]}
    rows = []
    for placement in placements:
        event, slot = events[placement["event_id"]], slots[placement["slot_id"]]
        room = rooms.get(placement["room_id"])
        needs_room = event["format"] == "ONSITE" and room is None
        rows.append({
            "id": placement["class_id"], "run": run_id, "event": placement["event_id"],
            "subject_name": event.get("subject_name", event.get("name", "")),
            "subject_id": event.get("subject_id"), "lesson_type": event["lesson_type"],
            "format": event["format"], "teacher_id": event.get("teacher_id"),
            "teacher_name": event.get("teacher_name"), "group_id": event.get("group_id"),
            "group_name": event.get("group_name") or snapshot.get("group_name"),
            "group_ids": event.get("blocking_group_ids", []),
            "stream_id": event.get("stream_id"), "stream_label": event.get("stream_name") or None,
            "project_id": event.get("project_id"), "project_title": event.get("project_title"),
            "student_ids": event.get("student_ids", []),
            "slot": placement["slot_id"], "slot_day": slot["day_of_week"],
            "slot_lesson_number": slot["lesson_number"],
            "slot_start_time": slot["start_time"], "slot_end_time": slot["end_time"],
            "week_pattern": snapshot["week_pattern_id"],
            "room": placement["room_id"], "room_name": room["name"] if room else None,
            "location_name": room.get("location_name", "") if room else "",
            "needs_room": needs_room, "room_status": "Нужно назначить аудиторию" if needs_room else "",
            "effective_capacity": room.get("effective_capacity") if room else None,
            "capacity_origin": room.get("capacity_origin", room.get("capacity_source")) if room else None,
            "display": event.get("subject_name", event.get("name", "")),
        })
    return rows


def _result(run, parent, placements):
    validation = validate_placements(run.input_snapshot, placements)
    return {
        "run": {"id": run.pk, "semester": run.semester_id, "module": run.module_id,
                "created_at": run.created_at.isoformat(), "status": run.status,
                "message": run.message, "objective_value": run.objective_value,
                "solve_time_seconds": run.solve_time_seconds, "solver_diagnostics": run.solver_diagnostics,
                "settings_snapshot": deepcopy(run.settings_snapshot)},
        "variant": _metadata(parent) if parent else None,
        "scheduled_classes": _calendar_rows(run.input_snapshot, placements, run.pk),
        "fixed_pd_snapshot": deepcopy(run.fixed_pd_snapshot),
        "issues": list(run.issues.values("id", "level", "text", "event_id")),
        "settings_snapshot": deepcopy(run.settings_snapshot),
        "input_snapshot": deepcopy(run.input_snapshot),
        "validation": validation, "placements": placements,
    }


def preview_variant(*, run_id, variant_id=None, changes=(), name=None):
    run, parent, placements = _load_source(run_id, variant_id)
    future = _apply_changes(run.input_snapshot, placements, changes)
    return _result(run, parent, future)


@transaction.atomic
def save_variant(*, run_id, variant_id=None, changes=(), name=None):
    run, parent, placements = _load_source(run_id, variant_id, lock=True)
    future = _apply_changes(run.input_snapshot, placements, changes)
    result = _result(run, parent, future)
    if not result["validation"]["valid"]:
        raise ManualVariantError("Известные конфликты блокируют сохранение варианта.", result["validation"])
    stored = deepcopy(result)
    stored.pop("variant", None)
    stored.pop("placements", None)
    variant = ManualScheduleVariant.objects.create(
        source_run=run, parent_variant=parent, name=(name or "Ручной вариант").strip(),
        validation=result["validation"], calendar_snapshot=stored)
    rows = {row["id"]: row for row in result["scheduled_classes"]}
    ManualPlacement.objects.bulk_create([
        ManualPlacement(variant=variant, source_class_id=item["class_id"],
                        event_id=item["event_id"], slot_id=item["slot_id"], room_id=item["room_id"],
                        calendar_row=rows[item["class_id"]]) for item in future])
    result["variant"] = _metadata(variant)
    return result


def suggest_rooms(*, run_id, class_id, variant_id=None, changes=(), name=None):
    run, _, placements = _load_source(run_id, variant_id)
    future = _apply_changes(run.input_snapshot, placements, changes)
    by_class = {item["class_id"]: item for item in future}
    if class_id not in by_class:
        raise ManualVariantError("Пара не принадлежит исходному расписанию.")
    events = {item.get("event_id", item.get("id")): item for item in run.input_snapshot["events"]}
    selected = by_class[class_id]
    if events[selected["event_id"]]["format"] != "ONSITE":
        return {"rooms": [], "validation": validate_placements(run.input_snapshot, future)}
    original = next(item for item in placements if item["class_id"] == class_id)
    room_map = {item["id"]: item for item in run.input_snapshot["rooms"]}
    reference = room_map.get(original["room_id"])
    available = []
    for room in room_map.values():
        candidate = deepcopy(future)
        next(item for item in candidate if item["class_id"] == class_id)["room_id"] = room["id"]
        validation = validate_placements(run.input_snapshot, candidate)
        if validation["valid"]:
            available.append({**deepcopy(room), "capacity_origin": room.get("capacity_origin", room.get("capacity_source")),
                              "validation": validation})
    if reference:
        available.sort(key=lambda room: room_nearness_key(reference, room))
    else:
        available.sort(key=lambda room: room["name"])
    return {"rooms": available, "validation": validate_placements(run.input_snapshot, future),
            "notice": "Близость по корпусу, этажу и номеру — эвристика; занятость проверена только в срезе запуска."}
