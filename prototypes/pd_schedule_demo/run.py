"""Run with .venv/bin/python -m prototypes.pd_schedule_demo.run."""

import argparse
import json
import os
from collections import Counter
from copy import deepcopy
from datetime import date, time
from html import escape
from itertools import combinations
from pathlib import Path
from tempfile import TemporaryDirectory


class FixturePortal:
    """Imitate only the observed read request; no authentication or network."""

    def __init__(self, fixture):
        self.fixture = fixture

    def students_in_project(self, portal_id):
        return deepcopy(self.fixture["roster_responses"][str(portal_id)])


def populate(fixture):
    from schedule_generator.enums import LessonType
    from schedule_generator.models import (
        Group,
        LectureStream,
        LessonEvent,
        Module,
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
    from schedule_generator.services.pd_import import import_fixed_pd

    semester = Semester.objects.create(name="OFFLINE DEMO 2026/2027", start_date=date(2026, 9, 1))
    module = Module.objects.create(semester=semester, number=1, start_date=date(2026, 9, 1),
                                   end_date=date(2026, 10, 31), teaching_weeks=9)
    pattern = WeekPattern.objects.create(semester=semester, name="Демо-неделя")
    times = [("09:00", "10:30"), ("10:40", "12:10"), ("12:20", "13:50"),
             ("14:30", "16:00"), ("16:10", "17:40"), ("17:50", "19:20"), ("19:30", "21:00")]
    for day in range(5):
        for number, (start, end) in enumerate(times, 1):
            TimeSlot.objects.create(week_pattern=pattern, day_of_week=day, lesson_number=number,
                                    start_time=time.fromisoformat(start), end_time=time.fromisoformat(end))
    rooms = {label: Room.objects.create(name=label, capacity=40)
             for label in ("Н405", "АВ4310", "Демо-аудитория")}
    portal = FixturePortal(fixture)
    groups, students, metadata, project_map = {}, {}, {}, []

    def student_for(row):
        if row["guid"] not in students:
            group = groups.setdefault(row["grp"], None)
            if group is None:
                group = groups[row["grp"]] = Group.objects.create(name=row["grp"], semester=semester)
            student = Student.objects.create(group=group, full_name=row["fio"])
            students[row["guid"]] = student
            metadata[student.pk] = {"fixture_guid": row["guid"], "name": row["fio"], "group": row["grp"]}
        return students[row["guid"]]

    mappings = []
    for entry in fixture["catalog"]:
        source = next(p for p in fixture["pd_document"]["projects"]
                      if p["source_number"] == entry["source_number"])
        if source["name"] != entry["title"]:
            raise ValueError("Название каталога не совпадает с проверенным расписанием")
        teacher = Teacher.objects.create(full_name=source["teacher_raw"])
        project = Project.objects.create(semester=semester, title=entry["title"], supervisor=teacher)
        for row in portal.students_in_project(entry["portal_id"]):
            ProjectEnrollment.objects.create(student=student_for(row), project=project)
        mappings.append({"source_number": entry["source_number"], "project_id": project.pk,
                         "teacher_id": teacher.pk, "rooms": {key: room.pk for key, room in rooms.items()}})
        project_map.append({**entry, "database_project_id": project.pk})
    for row in fixture["other_students"]:
        student_for(row)
    imported = import_fixed_pd(document=fixture["pd_document"], semester=semester, module=module,
        mapping={"reviewed": True, "source_sha256": fixture["pd_document"]["source"]["sha256"],
                 "projects": mappings}, travel_buffer_minutes=fixture["travel_buffer_minutes"])

    for name in ("Алгоритмы — практика", "Базы данных — лабораторная", "Математика — практика"):
        subject = Subject.objects.create(name=name)
        teacher = Teacher.objects.create(full_name=f"Демо-преподаватель: {name}")
        for group in groups.values():
            LessonEvent.objects.create(semester=semester, module=module, subject=subject,
                group=group, teacher=teacher,
                lesson_type=LessonType.LAB if "лабораторная" in name else LessonType.PRACTICE,
                pairs_per_week=2, audience_size=group.students.count())
    subject = Subject.objects.create(name="Общая лекция — демо")
    teacher = Teacher.objects.create(full_name="Демо-лектор")
    stream = LectureStream.objects.create(semester=semester, subject=subject, teacher=teacher)
    stream.groups.set(groups.values())
    LessonEvent.objects.create(semester=semester, module=module, subject=subject, stream=stream,
        teacher=teacher, lesson_type=LessonType.LECTURE, audience_size=len(students))
    return semester, module, pattern, metadata, project_map, imported


def report_for(outcome, fixture, metadata, project_map, imported):
    from schedule_generator.models import (
        GenerationRun,
        LessonEvent,
        ScheduledClass,
        Student,
    )

    run = GenerationRun.objects.get(pk=outcome.run_id)
    rows = []
    for lesson in ScheduledClass.objects.filter(run=run).select_related("event__subject", "slot", "room"):
        event, slot = lesson.event, lesson.slot
        group_ids = list(event.stream.groups.values_list("pk", flat=True)) if event.stream_id else [event.group_id]
        student_ids = list(Student.objects.filter(group_id__in=group_ids).values_list("pk", flat=True))
        rows.append({"kind": "class", "event_id": event.pk, "title": event.subject.name,
                     "day": slot.day_of_week, "start": slot.start_time.strftime("%H:%M"),
                     "end": slot.end_time.strftime("%H:%M"), "room_id": lesson.room_id,
                     "place": lesson.room.name if lesson.room else "Онлайн", "teacher_id": event.teacher_id,
                     "student_ids": student_ids})
    for fixed in run.fixed_pd_snapshot:
        rows.append({"kind": "pd", "title": fixed["project_title"], "day": fixed["day_of_week"],
                     "start": fixed["start"], "end": fixed["end"], "room_id": fixed["room_id"],
                     "place": fixed["place_details"].get("raw", ""), "teacher_id": fixed["teacher_id"],
                     "student_ids": fixed["student_ids"], "buffer": fixed["travel_buffer_minutes"]})

    conflicts = []
    def minute(value):
        return int(value[:2]) * 60 + int(value[3:])
    for first, second in combinations(rows, 2):
        if first["day"] != second["day"]:
            continue
        shared_people = (bool(set(first["student_ids"]) & set(second["student_ids"]))
                         or first["teacher_id"] == second["teacher_id"])
        shared_room = first["room_id"] is not None and first["room_id"] == second["room_id"]
        overlap = minute(first["start"]) < minute(second["end"]) and minute(second["start"]) < minute(first["end"])
        buffer = max(first.get("buffer", 0), second.get("buffer", 0))
        if shared_room or first["place"] == "Онлайн" or second["place"] == "Онлайн":
            buffer = 0
        travel_overlap = (minute(first["start"]) < minute(second["end"]) + buffer
                          and minute(second["start"]) < minute(first["end"]) + buffer)
        if (shared_room and overlap) or (shared_people and travel_overlap):
            conflicts.append([first["title"], second["title"]])
    actual = Counter(row["event_id"] for row in rows if row["kind"] == "class")
    expected = {e.pk: e.pairs_per_week for e in LessonEvent.objects.filter(semester=run.semester)}
    complete = all(actual[event] == count for event, count in expected.items())
    expected_pd = sum(len(schedule["meetings"]) for project in fixture["pd_document"]["projects"]
                      for schedule in project["schedules"])
    if (run.status != "SOLVED" or conflicts or not complete or run.issues.exists()
            or outcome.fixed_pd_count != expected_pd):
        raise ValueError(f"Демо не прошло проверку: status={run.status}, conflicts={conflicts}, complete={complete}")
    personal = [{**info, "schedule": sorted([row for row in rows if pk in row["student_ids"]],
                                           key=lambda row: (row["day"], row["start"]))}
                for pk, info in metadata.items()]
    return {"notice": fixture["notice"], "status": run.status, "project_mapping": project_map,
            "import": imported, "generated_classes": outcome.scheduled_count,
            "fixed_pd_sessions": outcome.fixed_pd_count, "conflicts": conflicts,
            "load_complete": complete, "travel_buffer_minutes": fixture["travel_buffer_minutes"],
            "students": personal}


def render(report):
    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    parts = ["<!doctype html><html lang='ru'><meta charset='utf-8'><title>ПД: персональные расписания</title>",
             "<style>body{font:16px system-ui;max-width:1100px;margin:35px auto;padding:0 20px}"
             "table{border-collapse:collapse;width:100%;margin-bottom:35px}td,th{padding:10px;text-align:left;"
             "border-bottom:1px solid #ddd}.pd{background:#e2f2ff}p{line-height:1.5}</style>",
             "<h1>ПД + учебные пары: четыре студента</h1>", f"<p>{escape(report['notice'])}</p>",
             f"<p><b>{report['status']}</b>: {report['generated_classes']} учебных пар, "
             f"{report['fixed_pd_sessions']} интервала ПД. Вся демо-нагрузка размещена. "
             f"Пересечений: {len(report['conflicts'])}. Запас на дорогу: {report['travel_buffer_minutes']} мин.</p>",
             "<p>Генерируется общий недельный шаблон трёх групп. Ниже — его проекции для студентов. "
             "Голубым выделена ПД; у студентов без выбора ПД её в расписании нет. "
             "Размеры групп и аудиторий, преподаватели обычных пар и нагрузка заданы для демо.</p>"]
    for student in report["students"]:
        parts.append(f"<h2>{escape(student['name'])} · {escape(student['group'])}</h2>"
                     "<table><tr><th>День</th><th>Время</th><th>Занятие</th><th>Место</th></tr>")
        for row in student["schedule"]:
            css = "pd" if row["kind"] == "pd" else ""
            parts.append(f"<tr class='{css}'><td>{days[row['day']]}</td><td>{row['start']}–{row['end']}</td>"
                         f"<td>{escape(row['title'])}</td><td>{escape(row['place'])}</td></tr>")
        parts.append("</table>")
    return "\n".join(parts) + "</html>"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/parser-prototype/activities/pd-personal-demo"))
    args = parser.parse_args()
    fixture = json.loads(Path(__file__).with_name("fixture.json").read_text())
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ["DB_ENGINE"] = "sqlite"
    os.environ.setdefault("SECRET_KEY", "offline-fixture-only")
    from django.conf import settings

    with TemporaryDirectory(prefix="polytech-pd-demo-") as temporary:
        # Set before django.setup(): never connect to the working database.
        settings.DATABASES["default"]["NAME"] = str(Path(temporary) / "db.sqlite3")
        import django
        django.setup()
        from django.core.management import call_command
        from django.db import connections

        from schedule_generator.services.generation import run_generation

        call_command("migrate", verbosity=0, interactive=False)
        semester, module, pattern, metadata, project_map, imported = populate(fixture)
        outcome = run_generation(semester=semester, module=module, week_pattern=pattern,
                                 clear_previous=False, seed=17, time_limit_seconds=10)
        report = report_for(outcome, fixture, metadata, project_map, imported)
        connections.close_all()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (args.output / "index.html").write_text(render(report))
    print(json.dumps({key: report[key] for key in ("status", "generated_classes", "fixed_pd_sessions",
                                                  "conflicts", "load_complete")}, ensure_ascii=False))
    print((args.output / "index.html").resolve())


if __name__ == "__main__":
    main()
