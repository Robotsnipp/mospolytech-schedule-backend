"""Импорт мероприятий из JSON-файла формата {"schema_version": 1, "events": [...]}."""

from datetime import date

from django.db import transaction

from .models import Event

SUPPORTED_SCHEMA_VERSIONS = {1}
DATE_FIELDS = ("date_start", "date_end", "announced_at")
TEXT_FIELDS = ("date_text", "source", "note")


class EventDataError(ValueError):
    """Файл целиком не подходит для импорта."""


def _parse_date(value):
    if value in (None, ""):
        return None
    return date.fromisoformat(value)


def _clean(raw):
    """Готовит поля модели из записи файла. Возвращает None, если запись плохая."""
    if not isinstance(raw, dict):
        return None
    title = (raw.get("title") or "").strip()
    url = (raw.get("url") or "").strip()
    scope = raw.get("scope")
    access = raw.get("access") or Event.Access.UNKNOWN
    if not title or not url:
        return None
    if scope not in Event.Scope.values or access not in Event.Access.values:
        return None
    try:
        dates = {name: _parse_date(raw.get(name)) for name in DATE_FIELDS}
    except (TypeError, ValueError):
        return None
    fields = {"scope": scope, "title": title, "url": url, "access": access, **dates}
    for name in TEXT_FIELDS:
        fields[name] = raw.get(name) or ""
    return fields


def import_events(data):
    """Создаёт или обновляет мероприятия. Ключ записи: url + title + date_start."""
    if not isinstance(data, dict):
        raise EventDataError("Ожидается JSON-объект с ключом events.")
    version = data.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise EventDataError(f"Неподдерживаемая schema_version: {version!r}.")
    events = data.get("events")
    if not isinstance(events, list):
        raise EventDataError("Поле events должно быть списком.")

    created = updated = skipped = 0
    with transaction.atomic():
        for raw in events:
            fields = _clean(raw)
            if fields is None:
                skipped += 1
                continue
            key = {name: fields.pop(name) for name in ("url", "title", "date_start")}
            _, was_created = Event.objects.update_or_create(**key, defaults=fields)
            if was_created:
                created += 1
            else:
                updated += 1
    return {"created": created, "updated": updated, "skipped": skipped}
