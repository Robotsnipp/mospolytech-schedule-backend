"""Resolve an explicit scenario pool without promoting assumptions to room facts."""

import re

from django.core.exceptions import ValidationError

from ..models import ResourceProfile, Room


def parse_pr_room(name):
    """User-confirmed Пр scheme only; keep suffixes and original labels elsewhere."""
    match = re.fullmatch(r"Пр\s*(\d)(\d)(\d{2})\s*([а-яa-z]?)", name, re.IGNORECASE)
    if not match:
        return None
    building, floor, number, suffix = match.groups()
    return {"building": int(building), "floor": int(floor),
            "number": int(number), "suffix": suffix.lower()}


def room_nearness_key(reference, candidate):
    """A numbering heuristic, never a claim about measured travel distance."""
    same_building = reference.get("building_id") == candidate.get("building_id")
    first, second = parse_pr_room(reference["name"]), parse_pr_room(candidate["name"])
    if same_building and first and second:
        return (0, first["floor"] != second["floor"],
                abs(first["floor"] - second["floor"]),
                abs(first["number"] - second["number"]), second["suffix"], candidate["name"])
    return (1 if same_building else 2, 0, 0, 0, "", candidate["name"])


def resolve_resource_profile(profile, semester, group):
    try:
        resource = ResourceProfile.objects.get(key=profile, semester=semester, group=group)
    except ResourceProfile.DoesNotExist as exc:
        raise ValidationError("Ресурсный профиль не подготовлен для выбранной группы и периода.") from exc
    definitions = resource.rooms
    ids = [item["room_id"] for item in definitions]
    if not ids or len(set(ids)) != len(ids):
        raise ValidationError("Ресурсный профиль пуст или содержит повторяющиеся аудитории.")
    rooms_by_id = {room.pk: room for room in Room.objects.filter(
        pk__in=ids, is_active=True).select_related("building__location")}
    if set(ids) != set(rooms_by_id):
        raise ValidationError("В ресурсном профиле отсутствуют или отключены аудитории.")
    rooms, snapshots = [], []
    for definition in definitions:
        room = rooms_by_id[definition["room_id"]]
        assumption = definition.get("capacity_assumption")
        if assumption is not None and (type(assumption) is not int or not 1 <= assumption <= 10000):
            raise ValidationError("Некорректное допущение вместимости.")
        effective = room.confirmed_capacity if room.confirmed_capacity is not None else assumption
        if effective is None:
            raise ValidationError(f"{room.name}: нет подтверждённой вместимости или допущения сценария.")
        snapshots.append({
            "id": room.pk, "name": room.name, "building_id": room.building_id,
            "building_name": room.building.name if room.building_id else "",
            "location_id": room.building.location_id if room.building_id else None,
            "location_name": room.location_name_for_ui(), "type": room.room_type,
            "allows_lecture": room.allows_lecture, "effective_capacity": effective,
            "confirmed_capacity": room.confirmed_capacity, "capacity_assumption": assumption,
            "capacity_source": "Подтверждённая вместимость" if room.confirmed_capacity is not None else "Допущение демо",
            "source": room.provenance, "profile": resource.key,
        })
        # The legacy solver reads capacity. This assignment is deliberately never saved.
        room.capacity = effective
        rooms.append(room)
    return rooms, snapshots


def available_resource_profiles():
    result = []
    for profile in ResourceProfile.objects.select_related("semester", "group").order_by("pk"):
        try:
            _, rooms = resolve_resource_profile(profile.key, profile.semester, profile.group)
        except ValidationError:
            continue
        result.append({"id": profile.key, "name": profile.name, "notice": profile.notice,
                       "semester_id": profile.semester_id, "group_id": profile.group_id,
                       "rooms": rooms, "provenance": profile.provenance})
    return result
