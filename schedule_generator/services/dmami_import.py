"""Atomic file import; no solver calls, fabricated resources or loss of source data."""

import hashlib
import json
from datetime import date, time

from django.db import transaction

from schedule_generator.models import (
    ImportedGroupSchedule,
    ImportedLesson,
    ScheduleImport,
)
from schedule_generator.services.dmami_teachers import link_imported_teachers


def parsed_value(value, parser):
    try:
        return parser(value) if value else None
    except (ValueError, TypeError):
        # Keep unreadable values in ImportedLesson.data, exactly as exported.
        return None


@transaction.atomic
def import_dmami_schedule(content, *, dry_run=False):
    document = json.loads(content.decode("utf-8-sig"))
    if not isinstance(document, dict) or document.get("kind") != "dmami_schedule" or document.get("schema_version") != 1:
        raise ValueError("Expected dmami_schedule schema_version=1 (schedule.json from parser)")
    if document.get("complete") is not True or document.get("errors"):
        raise ValueError("Refusing an incomplete export; resolve parser errors first")
    groups = document.get("groups")
    if not isinstance(groups, list) or not groups or document.get("exported_count") != len(groups):
        raise ValueError("Invalid or empty group list/count")
    source = document.get("source", {})
    metadata = document.get("source_metadata", {})
    if not isinstance(source, dict) or not isinstance(metadata, dict):
        raise ValueError("Invalid source metadata")
    fingerprint = hashlib.sha256(content).hexdigest()
    previous = ScheduleImport.objects.filter(file_sha256=fingerprint).first()
    if previous:
        teachers = link_imported_teachers(ImportedLesson.objects.filter(group__schedule_import=previous))
        if dry_run:
            transaction.set_rollback(True)
        return {"import_id": previous.pk, "groups": previous.group_count,
                "lessons": previous.lesson_count, "created": False, "dry_run": dry_run, **teachers}
    batch = ScheduleImport.objects.create(file_sha256=fingerprint, source=source, source_metadata=metadata)
    names, total = set(), 0
    for entry in groups:
        if not isinstance(entry, dict):
            raise ValueError("Invalid group entry")
        name = entry.get("name")
        if not isinstance(name, str) or not name or len(name) > 100 or name in names:
            raise ValueError("Invalid or duplicate group name")
        names.add(name)
        info, lessons = entry.get("group"), entry.get("lessons")
        if not isinstance(info, dict) or info.get("title") != name or not isinstance(lessons, list):
            raise ValueError(f"Invalid metadata/lessons of {name}")
        if not isinstance(entry.get("source", {}), dict) or not isinstance(entry.get("warnings", []), list):
            raise ValueError(f"Invalid provenance of {name}")
        group = ImportedGroupSchedule.objects.create(
            schedule_import=batch, name=name, metadata=info,
            source=entry.get("source", {}), warnings=entry.get("warnings", []),
        )
        paths, objects = set(), []
        for lesson in lessons:
            if not isinstance(lesson, dict) or lesson.get("group") != name:
                raise ValueError(f"Lesson group mismatch: {name}")
            path, day, slot = (lesson.get(key) for key in ("source_path", "day_of_week", "lesson_number"))
            if (not isinstance(path, str) or not path or len(path) > 200 or path in paths
                    or type(day) is not int or not 1 <= day <= 7
                    or type(slot) is not int or not 1 <= slot <= 32767
                    or not isinstance(lesson.get("subject"), str)):
                raise ValueError(f"Invalid or duplicate lesson in {name}: {path}")
            paths.add(path)
            objects.append(ImportedLesson(
                group=group, source_path=path, day_of_week=day, lesson_number=slot,
                subject=lesson["subject"], lesson_type=lesson.get("lesson_type") or "",
                teacher_raw=lesson.get("teacher_raw") or "", week_raw=lesson.get("week_raw") or "",
                date_from=parsed_value(lesson.get("date_from"), date.fromisoformat),
                date_to=parsed_value(lesson.get("date_to"), date.fromisoformat),
                start=parsed_value(lesson.get("start"), time.fromisoformat),
                end=parsed_value(lesson.get("end"), time.fromisoformat), data=lesson,
            ))
        ImportedLesson.objects.bulk_create(objects, batch_size=200)
        total += len(objects)
    batch.group_count, batch.lesson_count = len(names), total
    batch.save(update_fields=["group_count", "lesson_count"])
    teachers = link_imported_teachers(ImportedLesson.objects.filter(group__schedule_import=batch))
    result = {"import_id": None if dry_run else batch.pk, "groups": len(names),
              "lessons": total, "created": True, "dry_run": dry_run,
              "source_date": metadata.get("date"), **teachers}
    if dry_run:
        transaction.set_rollback(True)
    return result
