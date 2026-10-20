"""Stable source identities and explicit links for published schedule teachers."""

import hashlib
import re
import unicodedata

from django.db import transaction

from schedule_generator.models import Teacher


def teacher_name(value):
    return " ".join(unicodedata.normalize("NFKC", value).split())


def teacher_candidates(data, raw):
    structured = data.get("teachers")
    if structured:
        if not isinstance(structured, list) or any(not isinstance(item, dict) for item in structured):
            raise ValueError("Invalid structured teacher list")
        candidates = [(item.get("name"), item.get("id")) for item in structured]
    else:
        candidates = [(name, None) for name in raw.split(",")]
    result, warnings = {}, []
    for name, external_id in candidates:
        if not isinstance(name, str):
            raise ValueError("Invalid teacher name")
        name = teacher_name(name)
        if not name:
            continue
        if re.match(r"^ваканси", name.casefold()):
            warnings.append({"code": "vacancy", "label": name})
            continue
        if len(name) > 150:
            raise ValueError("Teacher name exceeds 150 characters")
        if external_id is not None:
            if isinstance(external_id, bool) or not re.fullmatch(r"[0-9]{1,30}", str(external_id)):
                raise ValueError("Invalid external teacher ID")
            identity = f"dmami:id:{int(external_id)}"
        else:
            identity = "dmami:name:" + hashlib.sha256(name.casefold().encode()).hexdigest()
        result[identity] = name
    return result, warnings


@transaction.atomic
def link_imported_teachers(lessons):
    """Also backfills old imports; repeated execution retains teacher PKs and links."""
    lessons = list(lessons)
    teachers = {teacher.dmami_identity: teacher for teacher in
                Teacher.objects.exclude(dmami_identity=None)}
    through = lessons[0].teachers.through if lessons else None
    links, updated, used, created = [], [], set(), 0
    skipped = 0
    for lesson in lessons:
        candidates, warnings = teacher_candidates(lesson.data, lesson.teacher_raw)
        skipped += len(warnings)
        for identity, name in candidates.items():
            teacher = teachers.get(identity)
            if teacher is None:
                teacher, was_created = Teacher.objects.get_or_create(
                    dmami_identity=identity,
                    defaults={"full_name": name, "dmami_name_key": name.casefold()},
                )
                teachers[identity] = teacher
                created += int(was_created)
            if teacher.dmami_name_key != name.casefold():
                warnings.append({"code": "source_name_changed", "label": name,
                                 "teacher_id": teacher.pk})
            used.add(teacher.pk)
            links.append(through(importedlesson_id=lesson.pk, teacher_id=teacher.pk))
        if lesson.teacher_warnings != warnings:
            lesson.teacher_warnings = warnings
            updated.append(lesson)
    if links:
        through.objects.bulk_create(links, ignore_conflicts=True, batch_size=200)
    if updated:
        type(lessons[0]).objects.bulk_update(updated, ["teacher_warnings"], batch_size=200)
    return {"teachers": len(used), "teachers_created": created,
            "teacher_links": len(links), "vacancy_labels": skipped}
