"""Демо-данные и запуск генерации расписания (OR-Tools CP-SAT).

Пример:
    uv run python manage.py generate_demo_schedule --time-limit 10
"""

from datetime import date, time

from django.core.management.base import BaseCommand
from django.db import transaction

from schedule_generator.enums import (
    Format,
    LessonType,
)
from schedule_generator.models import (
    Group,
    LectureStream,
    LessonEvent,
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
from schedule_generator.views import GenerateView


class Command(BaseCommand):
    help = "Создаёт демо-данные семестра и запускает генерацию расписания."

    def add_arguments(self, parser):
        parser.add_argument("--semester", default="2026/27 осень")
        parser.add_argument("--time-limit", type=int, default=30,
                            help="Лимит времени решателя, сек")
        parser.add_argument("--seed", type=int, default=17)

    def handle(self, *args, **opts):
        semester = self._populate(opts["semester"])
        wp = semester.week_patterns.first()
        result = GenerateView.run_generation(
            semester=semester,
            week_pattern=wp,
            time_limit_seconds=opts["time_limit"],
            seed=opts["seed"],
            clear_previous=True,
        )
        from schedule_generator.models import (
            GenerationRun,
        )
        run = GenerationRun.objects.get(pk=result["run_id"])
        self.stdout.write(self.style.SUCCESS(
            f"Run #{run.pk}: status={run.status}, "
            f"classes={run.scheduled_classes.count()}, "
            f"issues={run.issues.count()}, "
            f"time={run.solve_time_seconds:.2f}s, obj={run.objective_value}"
        ))
        for issue in run.issues.all():
            self.stdout.write(self.style.WARNING(f"  [{issue.level}] {issue.text}"))

    @transaction.atomic
    def _populate(self, name: str) -> Semester:
        semester, _ = Semester.objects.get_or_create(
            name=name, defaults={"start_date": date(2026, 9, 1), "weeks_count": 18},
        )

        # ---- сетка недель: пн-пт, 4 пары ----
        wp, _ = WeekPattern.objects.get_or_create(semester=semester, name="Базовая неделя")
        if not wp.slots.exists():
            for day in range(5):
                for num in range(1, 5):
                    TimeSlot.objects.create(
                        week_pattern=wp, day_of_week=day, lesson_number=num,
                        start_time=time(9 + 2 * (num - 1)),
                        end_time=time(10 + 2 * (num - 1)),
                    )

        # ---- аудитории ----
        rooms = {}
        for i in range(1, 11):
            rooms[i], _ = Room.objects.get_or_create(
                name=f"А-{100 + i}", defaults={"capacity": 30})
        for i in range(1, 3):
            rooms[f"big{i}"], _ = Room.objects.get_or_create(
                name=f"Аудитория {300 + i}", defaults={"capacity": 80})

        # ---- преподаватели ----
        teachers = {}
        for i in range(1, 9):
            teachers[i], _ = Teacher.objects.get_or_create(
                full_name=f"Преподаватель {i}")

        # ---- группы: две обычные и одна «перваки, первый семестр» ----
        groups = {}
        for gname, is_first in [("ИВМ-81", False), ("БИСО-82", False), ("ПИ-21", True)]:
            group, _ = Group.objects.get_or_create(
                name=gname,
                defaults={"semester": semester,
                          "is_first_year_first_semester": is_first},
            )
            groups[gname] = group
            if not group.students.exists():
                Student.objects.bulk_create([
                    Student(group=group, full_name=f"{gname} Студент {j}")
                    for j in range(1, 26)
                ])

        # ---- дисциплины ----
        subjects = {}
        for sname in ["Алгоритмы", "Базы данных", "Матанализ", "Физ-ра",
                      "Веб-разработка", "Клуб робототехники"]:
            subjects[sname], _ = Subject.objects.get_or_create(name=sname)

        # ---- лекции онлайн на поток из нескольких групп ----
        stream, _ = LectureStream.objects.get_or_create(
            semester=semester, subject=subjects["Алгоритмы"], teacher=teachers[1],
        )
        stream.groups.set(Group.objects.filter(semester=semester))
        LessonEvent.objects.get_or_create(
            semester=semester, subject=subjects["Алгоритмы"],
            lesson_type=LessonType.LECTURE, stream=stream,
            defaults={"teacher": teachers[1], "pairs_per_week": 1,
                      "audience_size": sum(g.students.count() for g in stream.groups.all())},
        )

        # ---- очные практики/лаборатории на каждую группу ----
        for gname, group in groups.items():
            LessonEvent.objects.get_or_create(
                semester=semester, subject=subjects["Базы данных"],
                lesson_type=LessonType.LAB, group=group,
                defaults={"teacher": teachers[2], "pairs_per_week": 1,
                          "audience_size": group.students.count()},
            )
            LessonEvent.objects.get_or_create(
                semester=semester, subject=subjects["Матанализ"],
                lesson_type=LessonType.PRACTICE, group=group,
                defaults={"teacher": teachers[3], "pairs_per_week": 2,
                          "audience_size": group.students.count()},
            )

        # ---- физкультура: есть в данных, но НЕ генерируется ----
        for group in groups.values():
            LessonEvent.objects.get_or_create(
                semester=semester, subject=subjects["Физ-ра"],
                lesson_type=LessonType.PE, group=group,
                defaults={"teacher": None, "pairs_per_week": 2,
                          "audience_size": group.students.count()},
            )

        # ---- ПД: студенты сами выбирают проект; группа ПД = выбравшие ----
        project, _ = Project.objects.get_or_create(
            semester=semester, title="Веб-портфолио ИТ-кампуса",
            defaults={"supervisor": teachers[4]},
        )
        for student in Student.objects.filter(group__in=groups.values())[:20]:
            ProjectEnrollment.objects.get_or_create(
                student=student, defaults={"project": project},
            )
        LessonEvent.objects.get_or_create(
            semester=semester, subject=subjects["Веб-разработка"],
            lesson_type=LessonType.PD, project=project,
            defaults={"teacher": teachers[4], "pairs_per_week": 1,
                      "audience_size": project.enrollments.count()},
        )

        # ---- внеучебка: очная и онлайн (формат задаётся у события) ----
        LessonEvent.objects.get_or_create(
            semester=semester, subject=subjects["Клуб робототехники"],
            lesson_type=LessonType.EXTRACURRICULAR,
            group=groups["ИВМ-81"], format=Format.ONSITE,
            defaults={"teacher": teachers[5], "pairs_per_week": 1, "audience_size": 15},
        )
        LessonEvent.objects.get_or_create(
            semester=semester, subject=subjects["Клуб робототехники"],
            lesson_type=LessonType.EXTRACURRICULAR,
            group=groups["БИСО-82"], format=Format.ONLINE,
            defaults={"teacher": teachers[5], "pairs_per_week": 1, "audience_size": 25},
        )

        return semester
