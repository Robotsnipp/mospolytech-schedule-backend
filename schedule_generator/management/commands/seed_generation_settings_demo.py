"""Add an isolated scenario; never rewrite old runs, slots, curricula or defaults."""

import json
from copy import deepcopy
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ...models import (
    Building,
    Group,
    LectureStream,
    LessonEvent,
    Location,
    Module,
    ResourceProfile,
    Room,
    Semester,
    Student,
)
from ...services.bells import create_bell_pattern

PROFILE_KEY = "generation-real-rooms-demo-v1"


@transaction.atomic
def seed_settings_demo(*, source_group_id=None):
    existing = ResourceProfile.objects.filter(key=PROFILE_KEY).first()
    if existing:
        return existing
    groups = Group.objects.filter(name__endswith=" · демо без ПД")
    source = groups.filter(pk=source_group_id).first() if source_group_id else groups.order_by("pk").last()
    if source is None:
        raise CommandError("Сначала подготовьте отдельный пример seed_generation_example без ПД.")
    if source.events.filter(project__isnull=False).exists():
        raise CommandError("Для копирования требуется подготовленная обычная нагрузка без ПД.")
    semester = Semester.objects.create(
        name="2026/27 · настройки · демо", start_date=source.semester.start_date,
        weeks_count=source.semester.weeks_count)
    group = Group.objects.create(
        name=source.name.replace(" · демо без ПД", " · демо настроек"), semester=semester,
        admission_year=source.admission_year, curriculum=source.curriculum,
        is_first_year_first_semester=source.is_first_year_first_semester)
    Student.objects.bulk_create([
        Student(group=group, full_name=f"Участник демо {number:02}")
        for number in range(1, source.students.count() + 1)
    ])
    modules = {}
    for original in source.semester.modules.all():
        modules[original.pk] = Module.objects.create(
            semester=semester, number=original.number, start_date=original.start_date,
            end_date=original.end_date, teaching_weeks=original.teaching_weeks)
    streams = {}
    for original in source.lecture_streams.filter(semester=source.semester):
        if set(original.groups.values_list("pk", flat=True)) != {source.pk}:
            raise CommandError("Общий поток нескольких групп не поддерживается этим демо.")
        stream = LectureStream.objects.create(
            semester=semester, subject=original.subject, teacher=original.teacher)
        stream.groups.add(group)
        streams[original.pk] = stream
    originals = LessonEvent.objects.filter(semester=source.semester)
    for original in originals:
        if original.group_id != source.pk and original.stream_id not in streams:
            continue
        fields = {field.attname: getattr(original, field.attname)
                  for field in LessonEvent._meta.concrete_fields if not field.primary_key}
        fields.update(semester_id=semester.pk, group_id=group.pk if original.group_id else None,
                      stream_id=streams[original.stream_id].pk if original.stream_id else None,
                      prepared_for_group_id=group.pk if original.prepared_for_group_id else None,
                      module_id=modules[original.module_id].pk if original.module_id else None)
        fields["preparation_source"] = {**deepcopy(original.preparation_source),
                                         "demo_source_event_id": original.pk,
                                         "demo_source_group_id": source.pk}
        event = LessonEvent(**fields)
        event.full_clean()
        event.save()
    create_bell_pattern(semester=semester, code="MOSCOW_DAY", name="Москва · 7 дней", days=7)
    fixture = Path(__file__).resolve().parents[2] / "fixtures/generation_real_rooms.json"
    document = json.loads(fixture.read_text())
    location, _ = Location.objects.get_or_create(
        name="Прянишникова", defaults={"address": "Москва, ул. Прянишникова, 2А"})
    room_definitions = []
    for item in document["rooms"]:
        building, _ = Building.objects.get_or_create(location=location, name=str(item["building"]))
        if Room.objects.filter(name=item["name"]).exists():
            raise CommandError(f"{item['name']}: требуется явное сопоставление существующей аудитории.")
        room = Room.objects.create(
            name=item["name"], building=building, room_type=item["type"], equipment=item["equipment"],
            # Legacy compatibility value has no authority; the resolver ignores it.
            capacity=0, confirmed_capacity=None, profile_only=True,
            allows_lecture=item["type"] == "Лекционная",
            provenance={**item["source"], "raw_code": item["raw_code"], "address": item["address"],
                        "accessibility": item["accessibility"], "capacity": None})
        room_definitions.append({"room_id": room.pk, "capacity_assumption": item["capacity_assumption"]})
    return ResourceProfile.objects.create(
        key=PROFILE_KEY, name="Реальные помещения · вместимость демо", semester=semester, group=group,
        rooms=room_definitions,
        notice=document["notice"] + " 30 вымышленных участников; ПД и физкультура не включены. "
               "Нагрузка плана набора 2025 скопирована из отдельного примера без изменения оригинала.",
        provenance={"source_group_id": source.pk, "source_semester_id": source.semester_id,
                    "catalog_fixture": str(fixture), "scope": "Только подготовленный срез; не весь вуз"})


class Command(BaseCommand):
    help = "Подготовить отдельный пример настроек с реальными помещениями и демовместимостью."

    def add_arguments(self, parser):
        parser.add_argument("--source-group", type=int)

    def handle(self, *args, **options):
        profile = seed_settings_demo(source_group_id=options["source_group"])
        self.stdout.write(json.dumps({"profile": profile.key, "semester": profile.semester_id,
                                     "group": profile.group_id}, ensure_ascii=False))
