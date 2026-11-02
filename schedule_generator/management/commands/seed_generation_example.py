"""Build a new, explicitly synthetic scheduling example from reviewed local inputs."""

import json
from copy import deepcopy
from datetime import date
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from schedule_generator.models import (
    Building,
    Curriculum,
    CurriculumEntry,
    FixedProjectSession,
    Group,
    Location,
    Module,
    Project,
    ProjectEnrollment,
    Room,
    Semester,
    Student,
    Teacher,
)
from schedule_generator.models.people import (
    DEMO_GROUP_SUFFIX,
    admission_year_from_group_name,
)
from schedule_generator.services.bells import create_bell_pattern
from schedule_generator.services.curriculum import (
    import_curriculum,
    prepare_module_load,
)

LEGACY_EXAMPLE_NOTICE = (
    "Учебный план реальный; состав, преподаватели, аудитории, ПД и распределение "
    "по модулям тестовые. Физкультура не включена."
)
EXAMPLE_NOTICE = (
    "Учебный план реальный; состав, преподаватели, аудитории и распределение "
    "по модулям тестовые. ПД и физкультура в этом примере не участвуют."
)
NO_PD_PROFILE = "moscow-day-no-pd-v1"
# A NEW synthetic fixture, not a transformation of published project meetings.
SYNTHETIC_PD_PAIRS = ((1, 2), (3, 3), (4, 5), (1, 1), (5, 5), (2, 2))


def _read_json(directory, name):
    return json.loads((directory / name).read_text(encoding="utf-8"))


def _save(instance):
    instance.full_clean()
    instance.save()
    return instance


@transaction.atomic
def seed_example(*, source_dir, semester_name, group_name="251-3210", include_synthetic_pd=False):
    """Create only fresh objects; refuse collisions instead of rewriting history."""
    directory = Path(source_dir)
    payload = _read_json(directory, "01-import-request.json")
    data = _read_json(directory, "data.json")
    base_group_name = group_name.removesuffix(DEMO_GROUP_SUFFIX)
    group_name = base_group_name if include_synthetic_pd else base_group_name + DEMO_GROUP_SUFFIX
    if admission_year_from_group_name(base_group_name) != data["admission_year"]:
        raise CommandError("Год набора в номере группы отличается от проверенного плана.")
    project_source = data.get("projects", []) if include_synthetic_pd else []
    notice = LEGACY_EXAMPLE_NOTICE if include_synthetic_pd else EXAMPLE_NOTICE
    if Semester.objects.filter(name=semester_name).exists():
        raise CommandError("Такой семестр уже существует; выберите новое имя или отдельную БД.")
    if Group.objects.filter(name=group_name).exists():
        raise CommandError("Такая группа генератора уже существует; существующие данные не меняются.")
    if include_synthetic_pd and len(project_source) != len(SYNTHETIC_PD_PAIRS):
        raise CommandError("Нужен проверенный пример с шестью синтетическими проектами.")

    modules_source = sorted(data["modules"], key=lambda item: item["number"])
    requests = {item["number"]: _read_json(directory, f"02-prepare-module-{item['number']}-request.json")
                for item in modules_source}
    load_teacher_ids = {item["teacher_id"] for request in requests.values() for item in request["entries"]}
    semester = _save(Semester(
        name=semester_name,
        start_date=date.fromisoformat(modules_source[0]["start_date"]),
        weeks_count=sum(item["teaching_weeks"] for item in modules_source),
    ))
    review = deepcopy(data["review"])
    # Keep a separate plan revision even when the source was imported previously.
    review["version"] = f"bell-example-{semester.pk}"
    imported = import_curriculum(document=payload["document"], review=review)
    plan = Curriculum.objects.get(pk=imported["curriculum_id"])
    plan.source = {
        **plan.source,
        "example_notice": notice,
        "example_seed": "moscow-day-v1" if include_synthetic_pd else NO_PD_PROFILE,
        "example_group_code": base_group_name,
    }
    plan.save(update_fields=["source"])
    group = _save(Group(
        name=group_name, admission_year=data["admission_year"],
        semester=semester, curriculum=plan,
    ))
    pattern = create_bell_pattern(semester=semester, code="MOSCOW_DAY")
    teachers = {
        item["id"]: _save(Teacher(full_name=f"{item['full_name']} · пример {semester.pk}"))
        for item in data["teachers"]
        if include_synthetic_pd or item["id"] in load_teacher_ids
    }
    students = {
        item["id"]: _save(Student(group=group, full_name=item["name"]))
        for item in data["students"]
    }
    location = _save(Location(name=f"Тестовая локация примера {semester.pk}"))
    building = _save(Building(location=location, name="Тестовый корпус"))
    room = _save(Room(
        name=f"Тестовая аудитория {semester.pk}", capacity=len(students), building=building,
    ))
    entries = {
        item.source["source_row"]: item
        for item in CurriculumEntry.objects.filter(curriculum=plan)
    }
    old_entry_to_row = {item["entry_id"]: item["source_row"] for item in data["fullness"]}
    projects = {}
    for item in project_source:
        projects[item["id"]] = _save(Project(
            semester=semester, title=item["title"], supervisor=teachers[item["teacher_id"]],
            max_students=len(item["student_ids"]),
        ))
        for old_student_id in item["student_ids"]:
            _save(ProjectEnrollment(student=students[old_student_id], project=projects[item["id"]]))

    output_modules = []
    for source_module in modules_source:
        module = _save(Module(
            semester=semester, number=source_module["number"],
            start_date=date.fromisoformat(source_module["start_date"]),
            end_date=date.fromisoformat(source_module["end_date"]),
            teaching_weeks=source_module["teaching_weeks"],
        ))
        request = requests[module.number]
        loads = [
            {
                **item,
                "curriculum_entry_id": entries[old_entry_to_row[item["curriculum_entry_id"]]].pk,
                "teacher_id": teachers[item["teacher_id"]].pk,
            }
            for item in request["entries"]
        ]
        prepared = prepare_module_load(
            group=group, module=module, semester_number=data["semester_number"],
            entries=loads, academic_hours_per_pair=request["academic_hours_per_pair"],
        )
        for index, item in enumerate(project_source):
            first, last = SYNTHETIC_PD_PAIRS[index]
            start = pattern.slots.get(day_of_week=item["day"], lesson_number=first).start_time
            end = pattern.slots.get(day_of_week=item["day"], lesson_number=last).end_time
            _save(FixedProjectSession(
                project=projects[item["id"]], module=module,
                teacher=teachers[item["teacher_id"]], day_of_week=item["day"],
                start_time=start, end_time=end,
                travel_buffer_minutes=item["buffer"],
                source_key=f"moscow-day-example:{module.number}:{index + 1}",
                place_details={
                    "raw": f"Тестовая дополнительная площадка {index + 1}",
                    "venue": {"kind": "additional"},
                    "resource_policy": {"reserve_room": False},
                },
                source={
                    "provider": "synthetic_generation_example", "version": "moscow-day-v1",
                    "original_interval": {"start": item["start"], "end": item["end"]},
                    "synthetic_pair_span": [first, last],
                    "notice": "Новая тестовая ПД, не опубликованное расписание проекта.",
                },
            ))
        output_modules.append({"id": module.pk, "number": module.number, **prepared})
    return {
        "semester_id": semester.pk, "group_id": group.pk, "group_name": group.name,
        "curriculum_id": plan.pk, "week_pattern_id": pattern.pk, "room_id": room.pk,
        "student_count": len(students), "project_count": len(projects),
        "modules": output_modules, "notice": notice,
        "profile": "moscow-day-v1" if include_synthetic_pd else NO_PD_PROFILE,
        "group_code": base_group_name, "admission_year": group.admission_year,
        "teacher_ids": sorted(item.pk for item in teachers.values()),
        "bell_schedule": pattern.bell_schedule,
    }


class Command(BaseCommand):
    help = "Создает новый изолированный пример 251-3210 из проверенных локальных JSON."

    def add_arguments(self, parser):
        parser.add_argument("--source-dir", required=True, type=Path)
        parser.add_argument("--semester-name")
        parser.add_argument("--group-name", default="251-3210")
        parser.add_argument("--with-synthetic-pd", action="store_true",
                            help="Явно воспроизвести исторический пример с вымышленной ПД.")

    def handle(self, *args, **options):
        try:
            result = seed_example(
                source_dir=options["source_dir"], semester_name=options["semester_name"] or (
                    "Пример 251-3210 · звонки Москвы 2026" if options["with_synthetic_pd"]
                    else "251-3210 · демо без ПД · осень 2026"),
                group_name=options["group_name"],
                include_synthetic_pd=options["with_synthetic_pd"],
            )
        except (OSError, ValueError, KeyError, ValidationError) as exc:
            raise CommandError(f"Пример не создан; изменения отменены: {exc}") from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
