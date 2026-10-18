"""Диагностика недостающих ресурсов при PARTIAL / INFEASIBLE.

Когда генерация не смогла разместить все обязательные пары, этого факта
само по себе мало: нужно сказать, ЧЕГО ИМЕННО не хватает. Модуль отвечает
«почему» — по каждому unscheduled-событию подбирает объяснение из данных
(слоты, аудитории, конфликты расписания) без повторного вызова решателя.

Гипотезы проверяются по возрастанию стоимости:
1. allowed_days  — ни одного слота в разрешённые дни недели;
2. вместимость   — очному событию не подходит ни одна активная аудитория;
3. дефицит слотов — свободных подходящих слотов меньше, чем нужно пар
                   (свободный = участник в этом слоте ещё никуда не
                   поставлен; для PARTIAL считаем по решению решателя,
                   для INFEASIBLE решения нет — берём худший случай:
                   все слоты «заняты конкурентами»);
4. иначе         — конкурс за общие ресурсы (мощности не хватает всем).

Порог точности: объяснения — эвристика над данными, а не формальное
доказательство минимальности IIS модели CP-SAT; они указывают реалистичную
причину и направление исправления (добавить слот/аудиторию/преподавателя).

Модуль знает только про модели и плоский GenerationResult; про CP-SAT —
ничего. Вызывается из services/result_writer.py на финализации запуска,
поэтому объяснения сохраняются в GenerationIssue и видны в dev-панели,
админке и API без дополнительных запросов.
"""

from __future__ import annotations

from collections import defaultdict

from ..enums import Format
from .dto import EventSpec, GenerationResult

_REASON_NO_ALLOWED_SLOTS = "нет ни одного слота в разрешённые дни недели"
_REASON_NO_ROOM = "нет активной аудитории, подходящей по вместимости"
_REASON_NOT_ENOUGH_SLOTS = "не хватает свободных слотов"
_REASON_GENERAL_SHORTAGE = ("участники формально свободны, но слоты/аудитории "
                            "делят с другими занятиями")


def build_shortage_explanations(*, specs: list[EventSpec],
                                generation_result: GenerationResult,
                                slots, rooms) -> dict[int, str]:
    """Объясняет, чего не хватает каждому unscheduled-событию.

    :param specs: события, участвовавшие в решении
    :param generation_result: результат решателя (берём assignments и
        unscheduled_event_ids); при INFEASIBLE assignments пуст — тогда
        занятость считается «весь ресурс уже роздан конкурентам»
    :param slots: список TimeSlot текущей недели (то, что подавали в модель)
    :param rooms: список Room с preloaded building
    :return: {event_id: человекочитаемое объяснение}
    """
    unscheduled_ids = set(generation_result.unscheduled_event_ids)
    if not unscheduled_ids:
        return {}

    spec_by_event_id = {spec.event_id: spec for spec in specs}
    day_of_week_by_slot_id = {slot.id: slot.day_of_week for slot in slots}

    # занятость участников по найденному решению:
    # {(kind, id): {slot_id, ...}} — «участник в этом слоте уже занят»
    busy_by_participant: dict[tuple[str, int], set[int]] = defaultdict(set)
    # {slot_id: {room_id, ...}} — аудитория занята в этом слоте
    busy_rooms_by_slot: dict[int, set[int]] = defaultdict(set)
    for assignment in generation_result.assignments:
        spec = spec_by_event_id.get(assignment["event_id"])
        if spec is None:
            continue
        slot_id = assignment["slot_id"]
        for group_id in spec.blocking_group_ids:
            busy_by_participant[("group", group_id)].add(slot_id)
        for student_id in spec.blocking_student_ids:
            busy_by_participant[("student", student_id)].add(slot_id)
        if spec.teacher_id is not None:
            busy_by_participant[("teacher", spec.teacher_id)].add(slot_id)
        if assignment.get("room_id") is not None:
            busy_rooms_by_slot[slot_id].add(assignment["room_id"])

    # INFEASIBLE: решения нет, но unscheduled = все события; чтобы не
    # выводить тривиальное «свободно 0 слотов», смоделируем гипотетическое
    # полное распределение ресурсов между всеми участниками.
    if generation_result.status == "INFEASIBLE":
        _simulate_full_occupancy(specs=specs,
                                 day_of_week_by_slot_id=day_of_week_by_slot_id,
                                 rooms=rooms,
                                 busy_by_participant=busy_by_participant,
                                 busy_rooms_by_slot=busy_rooms_by_slot)

    explanations: dict[int, str] = {}
    for event_id in sorted(unscheduled_ids):
        spec = spec_by_event_id.get(event_id)
        if spec is None:
            continue
        placed = sum(1 for a in generation_result.assignments
                     if a["event_id"] == event_id)
        missing = max(spec.pairs_per_week - placed, 0)
        explanations[event_id] = _explain_spec(
            spec=spec, missing=missing,
            day_of_week_by_slot_id=day_of_week_by_slot_id,
            busy_slots=_busy_slots_for_spec(spec, busy_by_participant),
            busy_rooms_by_slot=busy_rooms_by_slot,
            rooms=rooms,
        )
    return explanations


def _simulate_full_occupancy(*, specs: list[EventSpec],
                             day_of_week_by_slot_id: dict[int, int],
                             rooms,
                             busy_by_participant: dict[tuple[str, int], set[int]],
                             busy_rooms_by_slot: dict[int, set[int]]) -> None:
    """Для INFEASIBLE заполняет ресурсы «гипотетическими» парами конкурентов.

    Решения нет, поэтому оцениваем спрос сверху: жадно раздаём слоты всем
    событиям сразу (без решателя). Если даже при таком распределении для
    события не остаётся подходящих свободных слотов — дефицит реальный.
    Порядок обхода: сначала крупные события (больше шанс, что именно они
    «вытеснили» остальных).
    """
    ordered_specs = sorted(specs, key=lambda spec: (-spec.pairs_per_week,
                                                    spec.event_id))
    for spec in ordered_specs:
        allowed_slot_ids = [
            slot_id for slot_id, day_of_week in day_of_week_by_slot_id.items()
            if not spec.allowed_days or day_of_week in spec.allowed_days
        ]
        suitable_rooms = _suitable_rooms(spec, rooms)
        remaining = spec.pairs_per_week
        for slot_id in allowed_slot_ids:
            if remaining <= 0:
                break
            participants = _participants_of(spec)
            if any(slot_id in busy_by_participant.get(p, set())
                   for p in participants):
                continue
            if suitable_rooms:
                taken = busy_rooms_by_slot.setdefault(slot_id, set())
                free_room = next((room for room in suitable_rooms
                                  if room.id not in taken), None)
                if free_room is None:
                    continue
                taken.add(free_room.id)
            for participant in participants:
                busy_by_participant.setdefault(participant, set()).add(slot_id)
            remaining -= 1


def _participants_of(spec: EventSpec) -> list[tuple[str, int]]:
    """Все участники, которых событие блокирует в выбранном слоте."""
    participants = ([("group", group_id) for group_id in spec.blocking_group_ids]
                    + [("student", student_id) for student_id in spec.blocking_student_ids])
    if spec.teacher_id is not None:
        participants.append(("teacher", spec.teacher_id))
    return participants


def _busy_slots_for_spec(spec: EventSpec,
                         busy_by_participant: dict[tuple[str, int], set[int]]) -> set[int]:
    """Слоты, в которые событие нельзя поставить из-за занятости его участников."""
    busy: set[int] = set()
    for participant in _participants_of(spec):
        busy |= busy_by_participant.get(participant, set())
    return busy


def _suitable_rooms(spec: EventSpec, rooms) -> list:
    """Активные аудитории, вмещающие слушателей события (для очных)."""
    return [room for room in rooms
            if spec.format == Format.ONSITE and room.capacity >= spec.audience_size]


def _explain_spec(*, spec: EventSpec, missing: int,
                  day_of_week_by_slot_id: dict[int, int],
                  busy_slots: set[int], busy_rooms_by_slot: dict[int, set[int]],
                  rooms) -> str:
    """Одно объяснение по одному unscheduled-событию (см. docstring модуля)."""
    allowed_slot_ids = [
        slot_id for slot_id, day_of_week in day_of_week_by_slot_id.items()
        if not spec.allowed_days or day_of_week in spec.allowed_days
    ]
    base = (f"{_resource_label(spec)}: не хватает {missing} "
            f"{'пару' if missing == 1 else 'пар'} — ")

    if not allowed_slot_ids:
        return base + _REASON_NO_ALLOWED_SLOTS + "."

    suitable_rooms = _suitable_rooms(spec, rooms)
    if spec.format == Format.ONSITE and not suitable_rooms:
        return (base + _REASON_NO_ROOM
                + f" (слушателей: {spec.audience_size}, "
                  f"максимальная вместимость: "
                  f"{max((room.capacity for room in rooms), default=0)}).")

    # свободный подходящий слот = разрешённый день и все участники ещё
    # не заняты; для очных дополнительно — есть хотя бы одна подходящая
    # и ещё не занятая в этом слоте аудитория
    free_count = 0
    for slot_id in allowed_slot_ids:
        if slot_id in busy_slots:
            continue
        if suitable_rooms:
            taken_in_slot = busy_rooms_by_slot.get(slot_id, set())
            if all(room.id in taken_in_slot for room in suitable_rooms):
                continue
        free_count += 1

    if free_count < missing:
        return (base + _REASON_NOT_ENOUGH_SLOTS
                + f": свободно подходящих слотов {free_count}, "
                  f"нужно {missing}. Увеличьте число пар в шаблоне недели "
                  f"или разгрузите участников в эти дни.")
    return base + _REASON_GENERAL_SHORTAGE + " — добавьте аудитории или слоты."


def _resource_label(spec: EventSpec) -> str:
    """Что именно страдает — по типу носителя события."""
    if spec.stream_id is not None:
        return f"Лекция (поток #{spec.stream_id})"
    if spec.blocking_student_ids:
        return f"ПД ({len(spec.blocking_student_ids)} студентов)"
    kind = "Онлайн-занятие" if spec.format == Format.ONLINE else "Очное занятие"
    return f"{kind} (тип {spec.lesson_type})"


def _days_repr(days) -> str:
    """Список дней числами -> «пн, ср» (для текстов объяснений)."""
    day_names = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    return ", ".join(day_names[day] for day in sorted(days)) or "нет"
