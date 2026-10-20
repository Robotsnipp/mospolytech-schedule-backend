"""Демо-данные и запуск генерации расписания (OR-Tools CP-SAT).

Сетка недели соответствует правилам университета:
- пары идут с понедельника по субботу, 7 пар в день;
- на проектную деятельность (ПД) выделены пн и ср — в эти дни другие пары
  не планируются (см. services/day_rules.py);
- аудитории распределены по корпусам на разных локациях (час езды между
  ними): участник не может оказаться на двух локациях в одном слоте
  (см. add_location_consistency).

Объём демо-данных можно менять флагами --groups / --students-per-group /
--teachers / --projects (создаются только недостающие объекты, повторный
запуск идемпотентен).

Пример:
    uv run python manage.py generate_demo_schedule --time-limit 10 \\
        --groups 8 --students-per-group 24 --teachers 16 --projects 5
"""

import random
from datetime import date, time

from django.core.management.base import BaseCommand
from django.db import transaction

from schedule_generator.enums import (
    Chronotype,
    Format,
    LessonType,
)
from schedule_generator.models import (
    Building,
    GenerationRun,
    Group,
    LectureStream,
    LessonEvent,
    Location,
    Project,
    ProjectEnrollment,
    Room,
    Semester,
    Student,
    Subject,
    Teacher,
    TimeSlot,
    WeekPattern,
)
from schedule_generator.services import run_generation
from schedule_generator.services.day_rules import PD_ALLOWED_DAYS

# Реальное расписание пар университета: (номер пары, начало, конец)
LESSON_TIMES = [
    (1, time(9, 0), time(10, 30)),
    (2, time(10, 40), time(12, 10)),
    (3, time(12, 20), time(13, 50)),
    (4, time(14, 30), time(16, 0)),
    (5, time(16, 10), time(17, 40)),
    (6, time(17, 50), time(19, 20)),
    (7, time(19, 30), time(21, 0)),
]
# Учебные дни: 0=пн ... 5=сб (см. TimeSlot.DAY_CHOICES)
CLASS_DAYS = range(6)

# ---- справочники для наполненности демо-данных ----
FIRST_NAMES_M = ["Артём", "Дмитрий", "Иван", "Максим", "Кирилл", "Никита",
                 "Егор", "Тимур", "Марк", "Роман", "Глеб", "Даниил"]
FIRST_NAMES_F = ["Анна", "Мария", "Дарья", "Полина", "Виктория", "Ксения",
                 "Алина", "Вероника", "Ульяна", "Ева", "Милана", "Вера"]
LAST_NAMES = ["Иванов", "Петров", "Сидоров", "Кузнецов", "Соколов", "Попов",
              "Лебедев", "Козлов", "Новиков", "Морозов", "Волков", "Алексеев",
              "Тихонов", "Фёдоров", "Беляев", "Орлов", "Тарасов", "Жуков"]
PATRONYMICS = ["А.", "Б.", "В.", "Г.", "Д.", "Е.", "И.", "К.", "Л.", "М.",
               "Н.", "О.", "П.", "Р.", "С.", "Т."]

# Обычные учебные группы: (префикс названия, сколько групп такого профиля).
GROUP_PROFILES = [("ИВМ", 3), ("БИСО", 2), ("ПИ", 2), ("ИКВ", 2), ("АДП", 2)]

# Дисциплины обычных групп: (предмет, тип занятия, пары в неделю).
REGULAR_SUBJECTS = [
    ("Алгоритмы и структуры данных", LessonType.LECTURE, 1),
    ("Математический анализ", LessonType.PRACTICE, 2),
    ("Линейная алгебра", LessonType.PRACTICE, 1),
    ("Дискретная математика", LessonType.PRACTICE, 1),
    ("Базы данных", LessonType.LAB, 1),
    ("Операционные системы", LessonType.LAB, 1),
    ("Теория вероятностей", LessonType.PRACTICE, 1),
    ("История", LessonType.LECTURE, 1),
    ("Английский язык", LessonType.PRACTICE, 1),
]
# Дополнительные предметы для части групп — разнообразие нагрузки по группам.
EXTRA_SUBJECTS = [
    ("Компьютерное зрение", LessonType.LECTURE, 1),
    ("Обработка естественного языка", LessonType.PRACTICE, 1),
    ("Распределённые системы", LessonType.LAB, 1),
    ("Статистика", LessonType.PRACTICE, 1),
    ("Философия", LessonType.PRACTICE, 1),
]
# Предметы ПД (по одному на проект).
PD_SUBJECTS = ["Веб-разработка", "Мобильная разработка", "Data Science",
               "Интернет вещей", "Разработка игр"]
# Внеучебка: (предмет, очная ли).
CLUB_SUBJECTS = [("Клуб робототехники", True), ("Дебатный клуб", False),
                 ("Школа медиа продакшена", True)]
# Программы ДПО: (предмет, формат ONSITE/ONLINE).
DPO_PROGRAMS = [("Программирование на Python", Format.ONLINE),
                ("Сетевые технологии", Format.ONSITE),
                ("Machine Learning для инженеров", Format.ONLINE)]


def _student_name(rng: random.Random) -> str:
    first = rng.choice(FIRST_NAMES_F if rng.random() < 0.45 else FIRST_NAMES_M)
    return f"{rng.choice(LAST_NAMES)} {first} {rng.choice(PATRONYMICS)}"


class Command(BaseCommand):
    help = "Создаёт демо-данные семестра и запускает генерацию расписания."

    def add_arguments(self, parser):
        parser.add_argument("--semester", default="2026/27 осень")
        parser.add_argument("--time-limit", type=int, default=30,
                            help="Лимит времени решателя, сек")
        parser.add_argument("--seed", type=int, default=17)
        parser.add_argument("--groups", type=int, default=len(GROUP_PROFILES) * 2 + 1,
                            help="Сколько обычных учебных групп всего (включая перваков)")
        parser.add_argument("--students-per-group", type=int, default=24,
                            help="Студентов в обычной группе")
        parser.add_argument("--teachers", type=int, default=18,
                            help="Сколько преподавателей всего")
        parser.add_argument("--projects", type=int, default=len(PD_SUBJECTS),
                            help="Сколько проектов ПД")

    def handle(self, *args, **opts):
        rec_semester = self._populate(**opts)
        # Генерацию запускаем через сервисный слой — ту же точку входа,
        # что и HTTP API (schedule_generator/services/generation.py).
        outcome = run_generation(
            semester=rec_semester,
            week_pattern=rec_semester.week_patterns.first(),
            time_limit_seconds=opts["time_limit"],
            seed=opts["seed"],
            clear_previous=True,
        )
        run = GenerationRun.objects.get(pk=outcome.run_id)
        self.stdout.write(self.style.SUCCESS(
            f"Run #{run.pk}: status={run.status}, "
            f"classes={run.scheduled_classes.count()}, "
            f"issues={run.issues.count()}, "
            f"time={run.solve_time_seconds:.2f}s, obj={run.objective_value}"
        ))
        for issue in run.issues.all():
            self.stdout.write(self.style.WARNING(f"  [{issue.level}] {issue.text}"))

    @transaction.atomic
    def _populate(self, semester: str, seed: int, groups: int,
                  students_per_group: int, teachers: int, projects: int,
                  **_ignored) -> Semester:
        rec_semester, _ = Semester.objects.get_or_create(
            name=semester, defaults={"start_date": date(2026, 9, 1), "weeks_count": 18},
        )
        rng = random.Random(seed)

        # ---- сетка недель: пн-сб, 7 пар с реальным временем начала/конца ----
        week_pattern, _ = WeekPattern.objects.get_or_create(
            semester=rec_semester, name="Базовая неделя")
        if not week_pattern.slots.exists():
            for day_of_week in CLASS_DAYS:
                for lesson_number, start_time, end_time in LESSON_TIMES:
                    TimeSlot.objects.create(
                        week_pattern=week_pattern, day_of_week=day_of_week,
                        lesson_number=lesson_number,
                        start_time=start_time, end_time=end_time,
                    )

        # ---- локации, корпуса и аудитории ----
        # реальные локации университета (между каждой — час езды)
        lok_mih, _ = Location.objects.get_or_create(name="Михалковская")
        lok_pri, _ = Location.objects.get_or_create(name="Прянишникова")
        lok_semen, _ = Location.objects.get_or_create(name="Большая Семёновская")
        main_building, _ = Building.objects.get_or_create(
            location=lok_mih, name="Главный")
        lab_building, _ = Building.objects.get_or_create(
            location=lok_mih, name="Лабораторный")
        pri_building, _ = Building.objects.get_or_create(
            location=lok_pri, name="Корпус 1")

        self._ensure_rooms(main_building, prefix="А", first=101, count=15,
                           capacity=30, room_type="учебная")
        self._ensure_rooms(lab_building, prefix="Л", first=201, count=8,
                           capacity=24, room_type="лаборатория")
        self._ensure_rooms(main_building, prefix="Л", first=251, count=4,
                           capacity=24, room_type="лаборатория")
        self._ensure_rooms(pri_building, prefix="", first=301, count=8,
                           capacity=30, room_type="учебная")
        for lecture_index in range(1, 4):
            Room.objects.get_or_create(
                name=f"Зал {400 + lecture_index}",
                defaults={"capacity": 120, "building": main_building,
                          "room_type": "лекционный зал"})

        # ---- преподаватели ----
        # хронотипы чередуются, чтобы мягкое правило было видно на демо-данных
        chronotypes = [Chronotype.NEUTRAL, Chronotype.EARLY, Chronotype.LATE]
        for teacher_index in range(1, teachers + 1):
            Teacher.objects.get_or_create(
                full_name=f"Преподаватель {teacher_index}",
                defaults={"chronotype": chronotypes[teacher_index % len(chronotypes)]})
        teacher_list = list(Teacher.objects.order_by("id"))

        # ---- учебные группы: обычные, перваки и группа ПИ из старого демо ----
        regular_groups: list[Group] = []
        wanted_names: list[str] = []
        counter = {prefix: 1 for prefix, _ in GROUP_PROFILES}
        while len(wanted_names) < max(groups - 1, 0):
            for prefix, _count in GROUP_PROFILES:
                if len(wanted_names) >= groups - 1:
                    break
                if counter[prefix] <= _count:
                    wanted_names.append(f"{prefix}-{80 + counter[prefix]}")
                    counter[prefix] += 1
        wanted_names.append("ПИ-21")  # перваки, первый семестр (из старого демо)
        for group_name in wanted_names:
            group, created = Group.objects.get_or_create(
                name=group_name,
                defaults={"semester": rec_semester,
                          "is_first_year_first_semester": group_name == "ПИ-21"},
            )
            if created or group.semester_id != rec_semester.pk:
                group.semester = rec_semester
                group.save(update_fields=["semester"])
            regular_groups.append(group)
        legacy_group = Group.objects.filter(name__in=("ИВМ-81", "БИСО-82")).first()
        if legacy_group is not None and legacy_group not in regular_groups:
            regular_groups.append(legacy_group)

        # ---- студенты ----
        dpo_groups: list[Group] = []
        for group in regular_groups:
            if not group.students.exists():
                Student.objects.bulk_create([
                    Student(group=group, full_name=_student_name(rng))
                    for _ in range(students_per_group)
                ])
        # Группы ДПО: по одной на программу, слушателей меньше.
        for dpo_index, (_dpo_subject, _fmt) in enumerate(DPO_PROGRAMS, start=1):
            dpo_group, _ = Group.objects.get_or_create(
                name=f"ДПО-{dpo_index}",
                defaults={"semester": rec_semester, "is_dpo": True},
            )
            dpo_groups.append(dpo_group)
            if not dpo_group.students.exists():
                Student.objects.bulk_create([
                    Student(group=dpo_group, full_name=_student_name(rng))
                    for _ in range(12)
                ])

        # ---- дисциплины ----
        subject_names = ([name for name, _, _ in REGULAR_SUBJECTS]
                         + [name for name, _, _ in EXTRA_SUBJECTS]
                         + PD_SUBJECTS
                         + [name for name, _onsite in CLUB_SUBJECTS]
                         + [name for name, _fmt in DPO_PROGRAMS]
                         + ["Физ-ра"])
        subjects = {}
        for subject_name in subject_names:
            subjects[subject_name], _ = Subject.objects.get_or_create(name=subject_name)

        # ---- лекции онлайн на потоки из нескольких групп ----
        # Первая лекция — общий поток всех обычных групп (включая старый демо-поток);
        # остальные лекции делят группы попарно — у разных студентов разное расписание.
        lectures = [(name, pairs) for name, ltype, pairs in REGULAR_SUBJECTS
                    if ltype == LessonType.LECTURE]
        lectures += [(name, pairs) for name, ltype, pairs in EXTRA_SUBJECTS
                     if ltype == LessonType.LECTURE]
        non_first_year = [g for g in regular_groups if not g.is_first_year_first_semester]
        all_regular = Group.objects.filter(semester=rec_semester, is_dpo=False)
        teacher_cursor = 0
        for lecture_index, (subject_name, pairs) in enumerate(lectures):
            if lecture_index == 0:
                stream_groups = all_regular
            else:
                chunk = non_first_year[lecture_index % len(non_first_year):]
                stream_groups = Group.objects.filter(
                    pk__in=[g.pk for g in (chunk[:2] + non_first_year[:2])])
            teacher = teacher_list[teacher_cursor % len(teacher_list)]
            teacher_cursor += 1
            stream, _ = LectureStream.objects.get_or_create(
                semester=rec_semester, subject=subjects[subject_name], teacher=teacher,
            )
            stream.groups.set(stream_groups)
            audience_size = sum(g.students.count() for g in stream_groups)
            LessonEvent.objects.get_or_create(
                semester=rec_semester, subject=subjects[subject_name],
                lesson_type=LessonType.LECTURE, stream=stream,
                defaults={"teacher": teacher, "pairs_per_week": pairs,
                          "audience_size": audience_size},
            )

        # ---- очные практики/лаборатории на каждую учебную группу ----
        # Группы ДПО исключаем: на них по правилу LessonEvent.clean()
        # можно планировать только ДПО и внеучебку.
        onsite_subjects = [(name, ltype, pairs)
                           for name, ltype, pairs in REGULAR_SUBJECTS
                           if ltype in (LessonType.PRACTICE, LessonType.LAB)]
        extra_onsite = [(name, ltype, pairs)
                        for name, ltype, pairs in EXTRA_SUBJECTS
                        if ltype in (LessonType.PRACTICE, LessonType.LAB)]
        for group_index, group in enumerate(regular_groups):
            group_subjects = list(onsite_subjects)
            # Разнообразие: каждая третья группа получает доп. предметы,
            # а «счастливая» — вторую пару английского.
            if group_index % 3 == 0:
                group_subjects.append(extra_onsite[group_index % len(extra_onsite)])
            if group_index % 4 == 1:
                group_subjects.append(("Английский язык", LessonType.PRACTICE, 1))
            for subject_name, lesson_type, pairs in group_subjects:
                teacher = teacher_list[(teacher_cursor + group_index) % len(teacher_list)]
                LessonEvent.objects.get_or_create(
                    semester=rec_semester, subject=subjects[subject_name],
                    lesson_type=lesson_type, group=group,
                    defaults={"teacher": teacher, "pairs_per_week": pairs,
                              "audience_size": group.students.count()},
                )
            teacher_cursor += 1

        # ---- физкультура: есть в данных, но НЕ генерируется ----
        for group in regular_groups:
            LessonEvent.objects.get_or_create(
                semester=rec_semester, subject=subjects["Физ-ра"],
                lesson_type=LessonType.PE, group=group,
                defaults={"teacher": None, "pairs_per_week": 2,
                          "audience_size": group.students.count()},
            )

        # ---- ПД: несколько проектов; студенты выбирают проект сами ----
        # Дни ПД фиксированы правилами (services/day_rules.py): пн и ср.
        pd_students = Student.objects.filter(
            group__in=regular_groups,
            group__is_first_year_first_semester=False,
        ).order_by("id")
        project_list: list[Project] = []
        for project_index in range(projects):
            subject_name = PD_SUBJECTS[project_index % len(PD_SUBJECTS)]
            title = (f"{subject_name}: проект {project_index // len(PD_SUBJECTS) + 1}"
                     if project_index >= len(PD_SUBJECTS) else subject_name)
            project, _ = Project.objects.get_or_create(
                semester=rec_semester, title=title,
                defaults={"supervisor": teacher_list[project_index % len(teacher_list)]},
            )
            project_list.append(project)
            LessonEvent.objects.get_or_create(
                semester=rec_semester, subject=subjects[subject_name],
                lesson_type=LessonType.PD, project=project,
                defaults={"teacher": project.supervisor,
                          "pairs_per_week": len(PD_ALLOWED_DAYS),
                          "audience_size": project.enrollments.count()},
            )
        # Распределяем выбор проекта между студентами (кроме перваков).
        for student in pd_students:
            if ProjectEnrollment.objects.filter(student=student).exists():
                continue
            chosen = project_list[rng.randrange(len(project_list))]
            ProjectEnrollment.objects.create(student=student, project=chosen)
        for project in project_list:
            event = LessonEvent.objects.filter(project=project,
                                               lesson_type=LessonType.PD).first()
            if event is not None:
                event.audience_size = project.enrollments.count()
                event.save(update_fields=["audience_size"])

        # ---- внеучебка: очная и онлайн (формат задаётся у события) ----
        for club_index, (club_name, is_onsite) in enumerate(CLUB_SUBJECTS):
            # Каждая вторая обычная группа записана в клуб; размер — половина группы.
            for group in regular_groups[club_index::3]:
                LessonEvent.objects.get_or_create(
                    semester=rec_semester, subject=subjects[club_name],
                    lesson_type=LessonType.EXTRACURRICULAR,
                    group=group, format=Format.ONSITE if is_onsite else Format.ONLINE,
                    defaults={"teacher": teacher_list[(club_index + 5) % len(teacher_list)],
                              "pairs_per_week": 1,
                              "audience_size": max(group.students.count() // 2, 5)},
                )

        # ---- ДПО: отдельная группа на каждое ДПО; пары онлайн или очно ----
        for dpo_index, (dpo_group, (dpo_subject, dpo_format)) in enumerate(
                zip(dpo_groups, DPO_PROGRAMS, strict=True)):
            LessonEvent.objects.get_or_create(
                semester=rec_semester, subject=subjects[dpo_subject],
                lesson_type=LessonType.DPO, group=dpo_group, format=dpo_format,
                defaults={"teacher": teacher_list[(dpo_index + 8) % len(teacher_list)],
                          "pairs_per_week": 1,
                          "audience_size": dpo_group.students.count()},
            )

        return rec_semester

    @staticmethod
    def _ensure_rooms(building: Building, *, prefix: str, first: int, count: int,
                      capacity: int, room_type: str) -> None:
        """Создаёт недостающие аудитории вида «А-101», «Л-201», «301» ... в корпусе.

        Внутри корпуса аудитория может иметь буквенное деление (префикс «Г-»,
        «Л-») или только номера — это отражено в названии.
        """
        for offset in range(count):
            Room.objects.get_or_create(
                name=f"{prefix}{first + offset}" if prefix else f"{first + offset}",
                defaults={"capacity": capacity, "building": building,
                          "room_type": room_type},
            )
