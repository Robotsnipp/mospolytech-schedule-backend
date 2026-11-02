"""Validate a complete future placement set against its immutable run inputs.

No ORM lookups or solver calls: preview, commit and room suggestions share this
checker. Alternative variants do not reserve resources against one another.
"""

from itertools import combinations


def _issue(code, message, rows=(), **details):
    return {"code": code, "mode": "required", "message": message,
            "class_ids": [row.get("class_id") for row in rows if row.get("class_id") is not None],
            "event_ids": sorted({row["event_id"] for row in rows if row.get("event_id") is not None}),
            **details}


def _minute(value):
    if isinstance(value, (int, float)):
        return int(value)
    hours, minutes = map(int, value.split(":")[:2])
    return hours * 60 + minutes


def _interval(row):
    return (row.get("start_minute", _minute(row.get("start", "00:00"))),
            row.get("end_minute", _minute(row.get("end", "00:00"))))


def _overlap(a, b, buffer=0):
    return a["start"] < b["end"] + buffer and b["start"] < a["end"] + buffer


def validate_placements(snapshot, placements):
    """Return blocking conflicts, preferred deviations and uncheckable facts."""
    blocking, incomplete = [], []
    slots = {row["id"]: row for row in snapshot.get("slots", [])}
    events = {row.get("event_id", row.get("id")): row for row in snapshot.get("events", [])}
    rooms = {row["id"]: row for row in snapshot.get("rooms", [])}
    ordinary = []
    seen = set()
    for placement in placements:
        event, slot = events.get(placement["event_id"]), slots.get(placement["slot_id"])
        if event is None or slot is None:
            blocking.append(_issue("unknown_input", "Занятие или слот отсутствуют в снимке запуска.", [placement]))
            continue
        key = (placement["event_id"], placement["slot_id"])
        if key in seen:
            blocking.append(_issue("duplicate_placement", "Две пары одного занятия не могут занимать один слот.", [placement]))
        seen.add(key)
        start, end = slot.get("start_minute"), slot.get("end_minute")
        if start is None or end is None or start >= end:
            blocking.append(_issue("unknown_time", "В снимке нет корректного точного времени слота.", [placement]))
            continue
        room_id = placement.get("room_id")
        onsite = event.get("format") == "ONSITE"
        room = rooms.get(room_id)
        if room_id is not None and room is None:
            blocking.append(_issue("unknown_room", "Аудитория не входит в ресурсный пул запуска.", [placement], room_id=room_id))
        if not onsite and room_id is not None:
            blocking.append(_issue("online_room", "Онлайн-занятию аудитория не назначается.", [placement]))
        if onsite:
            if room_id is None:
                incomplete.append(_issue("room_unassigned", "Нужно назначить аудиторию; вместимость, корпус и дорога пока не проверены.", [placement]))
            elif room is not None:
                capacity = room.get("effective_capacity")
                if capacity is None:
                    incomplete.append(_issue("capacity_unknown", "Вместимость аудитории неизвестна.", [placement], room_id=room_id))
                elif capacity < event.get("audience_size", 0):
                    blocking.append(_issue("room_capacity", "Известная вместимость аудитории меньше числа слушателей.", [placement], actual=capacity, limit=event["audience_size"], room_id=room_id))
                if room.get("building_id") is None or room.get("location_id") is None:
                    incomplete.append(_issue("room_location_unknown", "Корпус или локация аудитории неизвестны; проверка дороги незавершена.", [placement]))
        allowed = event.get("allowed_days")
        if allowed and slot["day_of_week"] not in allowed:
            blocking.append(_issue("event_day", "День запрещён для этого занятия входными данными.", [placement]))
        ordinary.append({**placement, "start": start, "end": end, "day": slot["day_of_week"],
                         "lesson_number": slot["lesson_number"], "onsite": onsite,
                         "students": set(event.get("student_ids", [])),
                         "groups": set(event.get("blocking_group_ids", [])),
                         "teacher_id": event.get("teacher_id"), "room": room, "kind": "class"})
    fixed_rows = []
    for fixed in snapshot.get("fixed_pd", []):
        if fixed.get("occurrence_dates") == [] and fixed.get("date"):
            continue
        start, end = _interval(fixed)
        fixed_rows.append({
            "class_id": None, "event_id": None, "session_id": fixed["session_id"],
            "start": start, "end": end, "day": fixed["day_of_week"],
            "students": set(fixed.get("student_ids", [])), "groups": set(),
            "teacher_id": fixed.get("teacher_id"), "room_id": fixed.get("room_id"),
            "room": rooms.get(fixed.get("room_id")), "onsite": True, "kind": "pd",
            "buffer": fixed.get("travel_buffer_minutes", 0),
            "dates": fixed.get("occurrence_dates"), "place_raw": fixed.get("place_raw", ""),
        })
    for a, b in combinations(ordinary + fixed_rows, 2):
        if a["day"] != b["day"]:
            # A literal PD buffer can extend across midnight and the weekly edge.
            if (a["kind"] == "pd") != (b["kind"] == "pd"):
                fixed, other = (a, b) if a["kind"] == "pd" else (b, a)
                shared = (fixed["students"] & other["students"] or
                          fixed["teacher_id"] is not None and fixed["teacher_id"] == other["teacher_id"])
                same_room = fixed.get("room_id") is not None and fixed["room_id"] == other.get("room_id")
                if shared and other["onsite"] and other.get("room_id") is not None and not same_room:
                    fstart, fend = fixed["day"] * 1440 + fixed["start"], fixed["day"] * 1440 + fixed["end"]
                    ostart, oend = other["day"] * 1440 + other["start"], other["day"] * 1440 + other["end"]
                    buffer = fixed["buffer"]
                    if any(ostart < fend + shift + buffer and fstart + shift - buffer < oend
                           for shift in (-7 * 1440, 0, 7 * 1440)):
                        blocking.append(_issue("pd_travel_buffer", "Не хватает заданного запаса на дорогу к ПД.",
                                               [a, b], buffer_minutes=buffer, session_ids=[fixed["session_id"]]))
            continue
        if (a["kind"] == b["kind"] == "pd" and a["dates"] is not None
                and b["dates"] is not None and not set(a["dates"]) & set(b["dates"])):
            continue
        shared_students = a["students"] & b["students"]
        shared_groups = a["groups"] & b["groups"]
        shared_teacher = a["teacher_id"] is not None and a["teacher_id"] == b["teacher_id"]
        shared_people = shared_students or shared_groups or shared_teacher
        pair = [a, b]
        pd_ids = [row["session_id"] for row in pair if row["kind"] == "pd"]
        if _overlap(a, b):
            if shared_students or shared_groups:
                blocking.append(_issue("attendee_overlap", "Занятия пересекаются у слушателей.", pair, student_ids=sorted(shared_students), group_ids=sorted(shared_groups), session_ids=pd_ids))
            if shared_teacher:
                blocking.append(_issue("teacher_overlap", "Преподаватель занят в это время.", pair, teacher_id=a["teacher_id"], session_ids=pd_ids))
            if a.get("room_id") is not None and a["room_id"] == b.get("room_id"):
                blocking.append(_issue("room_overlap", "Аудитория занята в это время.", pair, room_id=a["room_id"], session_ids=pd_ids))
        if not shared_people or not (a["onsite"] and b["onsite"]):
            continue
        if (a["kind"] == "pd") != (b["kind"] == "pd"):
            fixed, other = (a, b) if a["kind"] == "pd" else (b, a)
            same_room = fixed.get("room_id") is not None and fixed["room_id"] == other.get("room_id")
            if other.get("room_id") is None:
                # Clearing a room is explicitly permitted, but cannot prove travel.
                continue
            if not same_room and _overlap(a, b, fixed["buffer"]):
                blocking.append(_issue("pd_travel_buffer", "Не хватает заданного запаса на дорогу к ПД.", pair, buffer_minutes=fixed["buffer"], session_ids=pd_ids))
        elif a["kind"] == b["kind"] == "class":
            loc_a = a["room"].get("location_id") if a["room"] else None
            loc_b = b["room"].get("location_id") if b["room"] else None
            distance = abs(a["lesson_number"] - b["lesson_number"])
            if loc_a is not None and loc_b is not None and loc_a != loc_b and distance <= snapshot.get("travel_buffer_slots", 1):
                blocking.append(_issue("ordinary_travel_buffer", "Между разными локациями нужен свободный слот на дорогу.", pair, buffer_slots=snapshot.get("travel_buffer_slots", 1)))
    # Personal rules use the same normalized definitions as the CP-SAT path.
    from .planning_rules import validate_rules
    personal = validate_rules(snapshot, placements)
    blocking.extend(personal.get("blocking", []))
    incomplete.extend(personal.get("incomplete", []))
    return {"valid": not blocking, "blocking": blocking,
            "warnings": personal.get("warnings", []), "incomplete": incomplete,
            "metrics": personal.get("metrics", {}),
            "rooms_complete": not any(row["code"] == "room_unassigned" for row in incomplete),
            "scope": "Только входной срез исходного запуска; занятость всего университета не подтверждена."}
