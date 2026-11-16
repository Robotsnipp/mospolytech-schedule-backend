"""Полный демо-пример одной командой, без внешних файлов.

    uv run python manage.py seed_demo            # создать (если демо уже есть - отказ)
    uv run python manage.py seed_demo --reset    # пересоздать демо

Что создаётся (все имена с пометкой «демо»):
- семестр, модуль на 8 недель, сетка звонков MOSCOW_DAY (пн-сб, 7 пар);
- локация, корпус, 4 аудитории; 5 дисциплин; 6 преподавателей;
- 2 группы по 12 студентов, лекционный поток и практики;
- запуск генерации CP-SAT основного расписания;
- программа ДПО: группа из 5 текущих студентов, 2 дисциплины, планирование
  поверх основного расписания;
- мероприятия из data/events/events_2025.json (если файл есть).

У одного преподавателя занижен недельный лимит, чтобы показать предупреждение.
Реальные данные (не «демо») команда не трогает.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ...enums import Format, LessonType
from ...models import (
    Building,
    DPODiscipline,
    DPOMembership,
    DPOProgram,
    GenerationRun,
    Group,
    LectureStream,
    LessonEvent,
    Location,
    Module,
    Room,
    ScheduledClass,
    Semester,
    Student,
    Subject,
    Teacher,
)
from ...services.bells import create_bell_pattern
from ...services.dpo import plan_program
from ...services.generation import run_generation

SEMESTER = "Демо · 2026/27 осень"
LOCATION = "Демо · Большая Семёновская"
GROUPS = ("269-901", "269-902")
DPO_GROUP = "ДПО-Python-демо"
MARK = " (демо)"
STUDENTS_PER_GROUP = 12

TEACHERS = {
    "math": "Иванова Анна Петровна",
    "prog": "Смирнов Олег Игоревич",
    "eng": "Кузнецова Мария Андреевна",
    "db": "Попов Денис Сергеевич",
    "hist": "Волкова Елена Викторовна",
    "python": "Орлов Илья Николаевич",
}
SUBJECTS = {
    "math": "Математика",
    "prog": "Программирование",
    "eng": "Английский язык",
    "db": "Базы данных",
    "hist": "История России",
    "python": "Python для анализа данных",
    "data": "Визуализация данных",
}
# Практики каждой группы: (дисциплина, пар в неделю)
PRACTICES = (("math", 1), ("prog", 2), ("eng", 1), ("db", 1), ("hist", 1))


class Command(BaseCommand):
    help = "Создаёт полный демо-пример: расписание, программу ДПО и мероприятия."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Удалить прежнее демо и создать заново.")
        parser.add_argument("--time-limit", type=int, default=20, help="Лимит решателя, секунд.")
        parser.add_argument("--skip-events", action="store_true", help="Не импортировать мероприятия.")

    def handle(self, *args, **options):
        if Semester.objects.filter(name=SEMESTER).exists():
            if not options["reset"]:
                raise CommandError("Демо уже создано. Запустите с --reset, чтобы пересоздать.")
            self.stdout.write("Удаляю прежнее демо...")
            delete_demo()
        data = create_demo_data()
        self.stdout.write(self.style.SUCCESS(
            f"Данные: семестр #{data['semester'].pk}, группы {', '.join(GROUPS)}, "
            f"{2 * STUDENTS_PER_GROUP} студентов, {len(TEACHERS)} преподавателей."))

        outcome = run_generation(semester=data["semester"], module=data["module"],
                                 time_limit_seconds=options["time_limit"], clear_previous=False)
        run = GenerationRun.objects.get(pk=outcome.run_id)
        self.stdout.write(self.style.SUCCESS(
            f"Основное расписание: запуск #{run.pk}, статус {run.status}, "
            f"пар {outcome.scheduled_count}, замечаний {outcome.issue_count}."))
        for issue in run.issues.order_by("pk"):
            self.stdout.write(f"  - {issue.text}")

        dpo = plan_program(data["program"], time_limit_seconds=options["time_limit"])
        self.stdout.write(self.style.SUCCESS(
            f"Программа ДПО «{data['program'].name}»: запуск #{dpo['run_id']}, статус {dpo['status']}, "
            f"пар {dpo['scheduled_count']} из {dpo['required_count']}, "
            f"учтено занятых пар основного расписания: {dpo['background_classes']}."))

        if not options["skip_events"]:
            self.stdout.write(import_demo_events())

        student = data["dpo_student"]
        self.stdout.write("\nЧто посмотреть (runserver, затем в браузере):")
        for line in (
            f"/api/schedule/schedule/groups/{data['groups'][0].pk}/?run_id={run.pk}  - расписание группы",
            f"/api/schedule/schedule/teachers/{data['prog_teacher'].pk}/?run_id={run.pk}  - преподаватель; "
            "в поле issues - предупреждение о недельном лимите",
            f"/api/schedule/schedule/students/{student.pk}/?semester={data['semester'].pk}"
            f"&module={data['module'].pk}  - студент: основные пары + ДПО",
            f"/api/schedule/dpo-programs/{data['program'].pk}/runs/  - запуски программы ДПО",
            "/api/events/?scope=city  - мероприятия Москвы",
            "/api/docs/  - Swagger",
        ):
            self.stdout.write(f"  {line}")


@transaction.atomic
def delete_demo():
    semester = Semester.objects.get(name=SEMESTER)
    # Порядок важен: слоты и модули защищены (PROTECT) от удаления, пока на них
    # ссылаются пары, запуски, нагрузка и программы ДПО.
    ScheduledClass.objects.filter(run__semester=semester).delete()
    GenerationRun.objects.filter(semester=semester).delete()
    LessonEvent.objects.filter(semester=semester).delete()
    DPOProgram.objects.filter(semester=semester).delete()
    LectureStream.objects.filter(semester=semester).delete()
    Group.objects.filter(semester=semester).delete()  # вместе со студентами и слушателями ДПО
    semester.delete()
    Teacher.objects.filter(full_name__endswith=MARK).delete()
    Subject.objects.filter(name__endswith=MARK).delete()
    Room.objects.filter(building__location__name=LOCATION).delete()
    Building.objects.filter(location__name=LOCATION).delete()
    Location.objects.filter(name=LOCATION).delete()


@transaction.atomic
def create_demo_data() -> dict:
    semester = Semester.objects.create(name=SEMESTER, start_date=date(2026, 9, 1), weeks_count=18)
    module = Module.objects.create(semester=semester, number=1, start_date=date(2026, 9, 1),
                                   end_date=date(2026, 10, 25), teaching_weeks=8)
    create_bell_pattern(semester=semester, code="MOSCOW_DAY", name="Демо · дневные пары")

    location = Location.objects.create(name=LOCATION, address="ул. Большая Семёновская, 38 (демо)")
    building = Building.objects.create(location=location, name="Корпус А (демо)")
    for number in (101, 102, 201, 202):
        Room.objects.create(name=f"А{number} (демо)", building=building, capacity=30, room_type="Учебная")

    teachers = {key: Teacher.objects.create(full_name=name + MARK) for key, name in TEACHERS.items()}
    # Занижаем лимит, чтобы показать предупреждение о недельной нагрузке.
    teachers["prog"].max_pairs_per_week = 3
    teachers["prog"].save()
    subjects = {key: Subject.objects.create(name=name + MARK) for key, name in SUBJECTS.items()}

    groups = [Group.objects.create(name=name, semester=semester) for name in GROUPS]
    students = {}
    for group in groups:
        students[group.pk] = [Student.objects.create(group=group, full_name=f"Студент {index:02d} {group.name}{MARK}")
                              for index in range(1, STUDENTS_PER_GROUP + 1)]

    stream = LectureStream.objects.create(semester=semester, subject=subjects["math"], teacher=teachers["math"])
    stream.groups.set(groups)
    LessonEvent.objects.create(semester=semester, module=module, subject=subjects["math"],
                               lesson_type=LessonType.LECTURE, stream=stream, teacher=teachers["math"],
                               pairs_per_week=1, audience_size=2 * STUDENTS_PER_GROUP)
    for group in groups:
        for key, pairs in PRACTICES:
            LessonEvent.objects.create(semester=semester, module=module, subject=subjects[key],
                                       lesson_type=LessonType.PRACTICE, group=group, teacher=teachers[key],
                                       pairs_per_week=pairs, audience_size=STUDENTS_PER_GROUP)

    dpo_group = Group.objects.create(name=DPO_GROUP, semester=semester, is_dpo=True)
    members = students[groups[0].pk][:3] + students[groups[1].pk][:2]
    for student in members:
        DPOMembership.objects.create(group=dpo_group, student=student)
    program = DPOProgram.objects.create(
        name="Python для анализа данных (демо)", semester=semester, module=module,
        description="Пример программы ДПО: слушатели - текущие студенты двух групп.")
    program.groups.add(dpo_group)
    DPODiscipline.objects.create(program=program, subject=subjects["python"], teacher=teachers["python"],
                                 format=Format.ONSITE, total_hours=16)   # 8 пар / 8 недель
    DPODiscipline.objects.create(program=program, subject=subjects["data"], teacher=teachers["python"],
                                 format=Format.ONLINE, total_pairs=8)
    return {"semester": semester, "module": module, "groups": groups, "program": program,
            "dpo_student": members[0], "prog_teacher": teachers["prog"]}


def import_demo_events() -> str:
    from events.importer import import_events

    path = Path(settings.BASE_DIR) / "data" / "events" / "events_2025.json"
    if not path.exists():
        return f"Мероприятия: файл {path} не найден, пропущено."
    result = import_events(json.loads(path.read_text(encoding="utf-8")))
    return (f"Мероприятия: создано {result['created']}, обновлено {result['updated']}, "
            f"пропущено {result['skipped']}.")
