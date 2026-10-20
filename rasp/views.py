"""Публичный read-only API расписания: /api/rasp/..."""

from __future__ import annotations

from datetime import date

from django.db.models import Count, Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import Group, Lesson, Room, Teacher
from .pair_times import PAIR_TIMES, VIRTUAL_LOCATIONS


def lesson_dict(lesson: Lesson, with_group: bool = False) -> dict:
    start, end = PAIR_TIMES.get(lesson.pair, (None, None))
    data = {
        "id": lesson.id, "subject": lesson.subject.name, "type": lesson.type,
        "weekday": lesson.weekday, "pair": lesson.pair,
        "start": start.strftime("%H:%M") if start else None,
        "end": end.strftime("%H:%M") if end else None,
        "date_from": lesson.date_from, "date_to": lesson.date_to, "dts": lesson.dts,
        "week": lesson.week, "location": lesson.location, "e_link": lesson.e_link,
        "is_session": lesson.is_session,
        "teachers": [{"id": t.id, "name": t.name, "is_vacancy": t.is_vacancy}
                     for t in lesson.teachers.all()],
        "rooms": [{"name": r.name, "location": r.location, "url": r.url}
                  for r in lesson.rooms.all()],
    }
    if with_group:
        data["group"] = lesson.group.title
    return data


def _lessons_qs(**filters):
    return (Lesson.objects.filter(**filters)
            .select_related("subject", "group").prefetch_related("teachers", "rooms"))


def _session(request) -> bool:
    return request.query_params.get("session") == "1"


def _group_dict(group: Group, lessons_count: int | None = None) -> dict:
    data = {"id": group.id, "title": group.title, "course": group.course,
            "date_from": group.date_from, "date_to": group.date_to,
            "evening": group.evening, "flag": group.flag, "comment": group.comment}
    if lessons_count is not None:
        data["lessons_count"] = lessons_count
    return data


@api_view(["GET"])
@permission_classes([])
def pair_times(request):
    return Response([{"pair": n, "start": s.strftime("%H:%M"), "end": e.strftime("%H:%M")}
                     for n, (s, e) in PAIR_TIMES.items()])


@api_view(["GET"])
@permission_classes([])
def groups(request):
    qs = Group.objects.annotate(lessons_count=Count("lessons")).order_by("title")
    query = request.query_params.get("q")
    if query:
        qs = qs.filter(title__icontains=query)
    if request.query_params.get("with_lessons") == "1":
        qs = qs.filter(lessons_count__gt=0)
    return Response([_group_dict(g, g.lessons_count) for g in qs])


@api_view(["GET"])
@permission_classes([])
def group_lessons(request, title):
    group = Group.objects.filter(title=title).first()
    if group is None:
        return Response({"detail": "Группа не найдена."}, status=status.HTTP_404_NOT_FOUND)
    lessons = _lessons_qs(group=group, is_session=_session(request))
    return Response({"group": _group_dict(group),
                     "lessons": [lesson_dict(item) for item in lessons]})


@api_view(["GET"])
@permission_classes([])
def teachers(request):
    qs = Teacher.objects.filter(lessons__isnull=False).distinct()
    query = request.query_params.get("q")
    if query:
        qs = qs.filter(name__icontains=query)
    if request.query_params.get("vacancies") != "1":
        qs = qs.filter(is_vacancy=False)
    return Response([{"id": t.id, "name": t.name, "is_vacancy": t.is_vacancy} for t in qs])


@api_view(["GET"])
@permission_classes([])
def teacher_lessons(request, teacher_id):
    teacher = Teacher.objects.filter(pk=teacher_id).first()
    if teacher is None:
        return Response({"detail": "Преподаватель не найден."}, status=status.HTTP_404_NOT_FOUND)
    lessons = _lessons_qs(teachers=teacher, is_session=_session(request))
    return Response({"teacher": {"id": teacher.id, "name": teacher.name,
                                 "is_vacancy": teacher.is_vacancy},
                     "lessons": [lesson_dict(item, with_group=True) for item in lessons]})


@api_view(["GET"])
@permission_classes([])
def free_rooms(request):
    """Аудитории, в которые нет занятий в заданный день недели/пару/дату."""
    try:
        weekday = int(request.query_params["weekday"])
        pair = int(request.query_params["pair"])
        day = (date.fromisoformat(request.query_params["date"])
               if request.query_params.get("date") else date.today())
    except (KeyError, ValueError):
        return Response({"detail": "Нужны weekday (1–7), pair (1–7) и, опционально, date=YYYY-MM-DD."},
                        status=status.HTTP_400_BAD_REQUEST)
    if weekday not in range(1, 8) or pair not in PAIR_TIMES:
        return Response({"detail": "weekday должен быть 1–7, pair — 1–7."},
                        status=status.HTTP_400_BAD_REQUEST)
    active = Q(date_from__isnull=True) | Q(date_from__lte=day)
    active &= Q(date_to__isnull=True) | Q(date_to__gte=day)
    busy = (Lesson.objects.filter(active, weekday=weekday, pair=pair, is_session=False)
            .values_list("rooms", flat=True))
    rooms = (Room.objects.filter(url__isnull=True)
             .exclude(location__in=VIRTUAL_LOCATIONS)
             .exclude(location__icontains="online").exclude(name__icontains="online").exclude(name__startswith="_").exclude(id__in=[b for b in busy if b])
             .order_by("location", "name"))
    query = request.query_params.get("q")
    if query:
        rooms = rooms.filter(Q(name__icontains=query) | Q(location__icontains=query))
    return Response([{"id": r.id, "name": r.name, "location": r.location} for r in rooms])
