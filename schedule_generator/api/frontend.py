"""API для фронтенда: агрегированные ответы под экраны приложения.

Отличие от CRUD-эндпоинтов (api/views.py): здесь один запрос возвращает всё,
что нужно одному экрану — клиент не собирает данные по N эндпоинтам и не
разбирает FK-идентификаторы. Ответы плоские, человекочитаемые, с готовой
структурой недельной сетки (дни -> слоты), чтобы сетку можно было отрисовать
без постобработки.

Группы эндпоинтов (префикс /api/schedule/frontend/, маршруты — в urls.py):

1. bootstrap/ — конфигурация UI одним запросом: справочники словарей
   (типы занятий, форматы, статусы генерации, дни недели) + семестры;
2. weeks/?semester=&pattern_id= — календарные недели периода (модули);
3. grid/holders/?semester=... — список носителей расписания (группы,
   преподаватели, потоки, проекты) для селектора на экране сетки;
4. grid/?holder_type=&holder_id=&semester=... — недельная сетка одного
   носителя: slots+days+classes (пустые ячейки уже разложены);
5. grid/calendar/?semester=&week=&pattern=... — сетка конкретного календарного
   недели с датами пар (недельный шаблон решателя привязывается к датам здесь,
   на сервере — см. services/calendar_view);
6. day/?date=&holder_type=&holder_id= — пары одного дня (мобильный вид «сегодня»);
7. me/students/<id>/, me/teachers/<id>/ — карточка «моё расписание»: профиль
   носителя + недельная сетка + служебные пометки (физкультура без слота).

Все представления — ViewSets c @extend_schema (summary/tags/parameters/
responses), чтобы Swagger UI показывал русские названия и точные схемы.
Слои: protocols (views) -> services/calendar_view (развёртка дат) -> models.
"""

from __future__ import annotations

import datetime as dt

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.response import Response

from ..enums import Format, LessonType
from ..models import (
    GenerationRun,
    Group,
    LectureStream,
    LessonEvent,
    Project,
    ScheduledClass,
    Semester,
    Student,
    Teacher,
    TimeSlot,
    WeekPattern,
)
from ..services.calendar_view import (
    CalendarWeek,
    build_calendar_weeks,
    resolve_pattern_for_date,
)
from .frontend_serializers import (
    BootstrapSerializer,
    CalendarGridSerializer,
    DayScheduleSerializer,
    GridHolderListSerializer,
    MeResponseSerializer,
    SemesterBriefSerializer,
    WeekMatrixSerializer,
    WeekListSerializer,
)
from .serializers import FrontendGridFilterSerializer

HOLDER_TYPES = ("group", "stream", "teacher", "student", "project")


def _latest_run(semester: Semester, module=None) -> GenerationRun | None:
    """Последний успешный запуск периода (SOLVED/PARTIAL) или None."""
    qs = GenerationRun.objects.filter(semester=semester)
    if module is not None:
        qs = qs.filter(module=module)
    else:
        qs = qs.filter(module__isnull=True)
    return (qs.filter(status__in=["SOLVED", "PARTIAL"])
            .order_by("-created_at").first())


def _holder_filter(holder_type: str, holder_id: int) -> Q:
    """Условия отбора пар из результата генерации для одного носителя."""
    if holder_type == "group":
        return Q(event__group_id=holder_id) | Q(event__stream__groups_id=holder_id)
    if holder_type == "stream":
        return Q(event__stream_id=holder_id)
    if holder_type == "teacher":
        return Q(event__teacher_id=holder_id)
    if holder_type == "project":
        return Q(event__project_id=holder_id)
    if holder_type == "student":
        student = (Student.objects.select_related("group", "project_choice")
                   .filter(pk=holder_id).first())
        if student is None:
            return Q(pk=None)  # пустой результат вместо 500
        cond = (Q(event__group_id=student.group_id)
                | Q(event__stream__groups_id=student.group_id))
        if student.project_choice_id:
            cond |= Q(event__project_id=student.project_choice.project_id)
        return cond
    return Q(pk=None)


def _load_classes(run: GenerationRun, holder_filter: Q,
                  week_pattern_id: int | None = None):
    qs = (ScheduledClass.objects
          .filter(run=run)
          .filter(holder_filter)
          .select_related("slot", "room", "event", "event__subject",
                          "event__group", "event__stream", "event__project",
                          "event__teacher"))
    if week_pattern_id is not None:
        qs = qs.filter(slot__week_pattern_id=week_pattern_id)
    return list(qs)


def _class_cell(sc: ScheduledClass) -> dict:
    """Пара в компактном виде для ячейки сетки (только нужное фронту)."""
    event = sc.event
    holder = (event.group.name if event.group_id
              else str(event.stream) if event.stream_id
              else event.project.title if event.project_id else "")
    room = sc.room
    location = room.location_name_for_ui() if room is not None else ""
    return {
        "id": sc.pk,
        "subject": event.subject.name,
        "lesson_type": event.lesson_type,
        "lesson_type_display": LessonType(event.lesson_type).label,
        "format": event.effective_format,
        "format_display": Format(event.effective_format).label,
        "holder": holder,
        "teacher": event.teacher.full_name if event.teacher_id else "",
        "room": room.name if room else ("Онлайн" if
                                        event.effective_format == Format.ONLINE else ""),
        "location": location,
    }


DAY_NAMES = dict(TimeSlot.DAY_CHOICES)


def _build_matrix(pattern: WeekPattern, classes: list[ScheduledClass]) -> dict:
    """Недельная матрица: все слоты шаблона, пары разложены по ячейкам."""
    by_slot: dict[int, list[dict]] = {}
    for sc in classes:
        by_slot.setdefault(sc.slot_id, []).append(_class_cell(sc))
    days: dict[int, dict] = {}
    for slot in pattern.slots.all().order_by("day_of_week", "lesson_number"):
        day = days.setdefault(slot.day_of_week, {
            "day_of_week": slot.day_of_week,
            "name": DAY_NAMES.get(slot.day_of_week, str(slot.day_of_week)),
            "slots": [],
        })
        day["slots"].append({
            "slot_id": slot.pk,
            "lesson_number": slot.lesson_number,
            "start_time": slot.start_time,
            "end_time": slot.end_time,
            "classes": by_slot.get(slot.pk, []),
        })
    ordered_days = [days[d] for d in sorted(days)]
    return {
        "week_pattern": {
            "id": pattern.pk,
            "name": pattern.name,
            "parity": pattern.parity,
        },
        "days": ordered_days,
    }


# ---------------------------------------------------------------------------
#bootstrap/weeks/holders
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(
        summary="[Фронт] Словари и семестры для инициализации UI",
        description="Один запрос: варианты enum'ов (типы занятий, форматы, "
                    "статусы генерации, дни недели) и список семестров.",
        tags=["Frontend"],
        responses=BootstrapSerializer,
    ),
)
class BootstrapViewSet(viewsets.ViewSet):
    """GET /api/schedule/frontend/bootstrap/ — стартовая конфигурация фронта."""

    permission_classes: list = []

    def list(self, request):
        semesters = Semester.objects.order_by("-start_date")
        data = {
            "lesson_types": [{"value": v, "label": l}
                             for v, l in LessonType.choices],
            "formats": [{"value": v, "label": l} for v, l in Format.choices],
            "run_statuses": [{"value": v, "label": l}
                             for v, l in GenerationRun.STATUS_CHOICES],
            "week_parity": [{"value": 0, "label": "Любая"},
                            {"value": 1, "label": "Нечётная"},
                            {"value": 2, "label": "Чётная"}],
            "semesters": [{"id": s.pk, "name": s.name,
                           "start_date": s.start_date,
                           "weeks_count": s.weeks_count}
                          for s in semesters],
        }
        return Response(data)


@extend_schema_view(
    list=extend_schema(
        summary="[Фронт] Календарные недели семестра",
        description="Модули семестра с диапазоном дат и числом учебных недель — "
                    "для переключателя «модуль/неделя» на экране расписания.",
        tags=["Frontend"],
        parameters=[OpenApiParameter("semester", int, required=False,
                                     description="ID семестра; по умолчанию — "
                                                 "последний по дате начала.")],
        responses=WeekListSerializer(many=True),
    ),
)
class PeriodWeekViewSet(viewsets.ViewSet):
    """GET /api/schedule/frontend/weeks/?semester=..."""

    permission_classes: list = []

    def list(self, request):
        semester_id = request.query_params.get("semester")
        if semester_id:
            semester = Semester.objects.filter(pk=semester_id).first()
        else:
            semester = Semester.objects.order_by("-start_date").first()
        if semester is None:
            return Response({"detail": "Семестр не найден."}, status=404)
        weeks = []
        for m in semester.modules.order_by("number"):
            monday = m.start_date - dt.timedelta(days=m.start_date.weekday())
            weeks.append({
                "module_id": m.pk,
                "number": m.number,
                "start_date": m.start_date,
                "end_date": m.end_date,
                "teaching_weeks": m.teaching_weeks,
                "first_monday": monday,
            })
        return Response({"semester": SemesterBriefSerializer(semester).data,
                         "weeks": weeks})


@extend_schema_view(
    list=extend_schema(
        summary="[Фронт] Носители расписания для селектора",
        description="Компактные списки групп, преподавателей, лекционных "
                    "потоков и проектов ПД — параметр grid/?holder_type=&holder_id=.",
        tags=["Frontend"],
        parameters=[
            OpenApiParameter("semester", int, required=False,
                             description="Фильтр по семестру (для потоков и проектов)."),
            OpenApiParameter("q", str, required=False,
                             description="Поиск по названию/ФИО."),
        ],
        responses=GridHolderListSerializer,
    ),
)
class GridHolderViewSet(viewsets.ViewSet):
    """GET /api/schedule/frontend/grid/holders/"""

    permission_classes: list = []

    def list(self, request):
        params = request.query_params
        semester_id = params.get("semester")
        q = (params.get("q") or "").strip()

        groups = Group.objects.order_by("name")
        teachers = Teacher.objects.order_by("full_name")
        streams = LectureStream.objects.prefetch_related("groups").order_by("subject__name")
        projects = Project.objects.order_by("title")
        if semester_id:
            streams = streams.filter(semester_id=semester_id)
            projects = projects.filter(semester_id=semester_id)
        if q:
            groups = groups.filter(Q(name__icontains=q))
            teachers = teachers.filter(Q(full_name__icontains=q))
            streams = streams.filter(Q(subject__name__icontains=q))
            projects = projects.filter(Q(title__icontains=q))

        return Response({
            "groups": [{"id": g.pk, "name": g.name, "is_dpo": g.is_dpo}
                       for g in groups[:200]],
            "teachers": [{"id": t.pk, "full_name": t.full_name}
                         for t in teachers[:200]],
            "streams": [{"id": s.pk, "label": str(s),
                         "groups": [g.name for g in s.groups.all()]}
                        for s in streams[:200]],
            "projects": [{"id": p.pk, "title": p.title,
                          "supervisor": (p.supervisor.full_name
                                         if p.supervisor_id else "")}
                         for p in projects[:200]],
        })


# ---------------------------------------------------------------------------
#grid (матрица недели + календарь)
# ---------------------------------------------------------------------------

GRID_PARAMS = [
    OpenApiParameter("holder_type", str, required=True,
                     description=f"Носитель: {'|'.join(HOLDER_TYPES)}."),
    OpenApiParameter("holder_id", int, required=True, description="ID носителя."),
    OpenApiParameter("semester", int, required=True, description="ID семестра."),
    OpenApiParameter("module", int, required=False,
                     description="ID модуля (если в семестре есть модульные занятия)."),
    OpenApiParameter("run", int, required=False,
                    description="ID запуска; по умолчанию — последний SOLVED/PARTIAL."),
    OpenApiParameter("pattern", int, required=False,
                     description="ID шаблона недели; по умолчанию — первый паттерн семестра."),
]


@extend_schema_view(
    list=extend_schema(
        summary="[Фронт] Недельная сетка носителя",
        description="Готовая матрица недели: дни, слоты, пары в ячейках. "
                    "Пустые ячейки включены — фронт рисует сетку без склейки.",
        tags=["Frontend"],
        parameters=GRID_PARAMS,
        responses=WeekMatrixSerializer,
    ),
)
class WeekGridViewSet(viewsets.ViewSet):
    """GET /api/schedule/frontend/grid/ — недельный шаблон одного носителя."""

    permission_classes: list = []

    def list(self, request):
        serializer = FrontendGridFilterSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        semester = data["semester"]
        run = data.get("run") or _latest_run(semester, data.get("module"))
        pattern = data.get("week_pattern") or WeekPattern.objects.filter(
            semester=semester).order_by("id").first()
        if run is None or pattern is None:
            return Response({
                "detail": ("Для выбранного периода ещё нет успешной генерации "
                           "или шаблона недели."),
                "run_id": run.pk if run else None,
                "week_pattern": None,
                "days": [],
            })
        classes = _load_classes(run, _holder_filter(data["holder_type"],
                                                    data["holder_id"]),
                                week_pattern_id=pattern.pk)
        matrix = _build_matrix(pattern, classes)
        return Response({"run_id": run.pk, **matrix})


@extend_schema_view(
    list=extend_schema(
        summary="[Фронт] Сетка календарной недели с датами",
        description="Разворачивает недельный шаблон решателя на конкретную "
                    "неделю модуля: каждая пара получает ISO-дату. Параметры: "
                    "week — номер учебной недели модуля (1..N), либо date — "
                    "любая дата внутри нужной недели; parity выбирает чётную/"
                    "нечётную сетку автоматически по номеру недели.",
        tags=["Frontend"],
        parameters=[
            *GRID_PARAMS,
            OpenApiParameter("week", int, required=False,
                             description="Номер учебной недели модуля (1..N)."),
            OpenApiParameter("date", dt.date, required=False,
                             description="Дата внутри нужной недели "
                                         "(альтернатива week)."),
        ],
        responses=CalendarGridSerializer,
    ),
)
class CalendarGridViewSet(viewsets.ViewSet):
    """GET /api/schedule/frontend/grid/calendar/ — сетка недели с датами."""

    permission_classes: list = []

    def list(self, request):
        base = FrontendGridFilterSerializer(data={
            **{k: v for k, v in request.query_params.items()
               if k in ("holder_type", "holder_id", "semester", "module",
                        "run", "pattern")}})
        base.is_valid(raise_exception=True)
        data = base.validated_data
        semester = data["semester"]
        module = data.get("module")
        if module is None:
            module = semester.modules.order_by("number").first()
        if module is None:
            return Response({"detail": "У семестра нет модулей с датами."},
                            status=404)
        weeks = build_calendar_weeks(module)
        if not weeks:
            return Response({"detail": "У модуля нет учебных недель."},
                            status=404)

        week_no = request.query_params.get("week")
        date_raw = request.query_params.get("date")
        week: CalendarWeek | None = None
        if week_no:
            week = next((w for w in weeks if w.number == int(week_no)), None)
        elif date_raw:
            try:
                target = dt.date.fromisoformat(date_raw)
            except ValueError:
                return Response({"detail": "Некорректная дата (ожидается ГГГГ-ММ-ДД)."},
                                status=400)
            week = next((w for w in weeks if w.start_date <= target <= w.end_date),
                        None)
        if week is None:
            week = weeks[0]

        pattern = resolve_pattern_for_date(semester, week)
        run = data.get("run") or _latest_run(semester, module)
        classes = (_load_classes(run, _holder_filter(data["holder_type"],
                                                     data["holder_id"]),
                                 week_pattern_id=pattern.pk)
                   if run and pattern else [])
        matrix = _build_matrix(pattern, classes) if pattern else {
            "week_pattern": None, "days": []}
        payload = {
            "module": {"id": module.pk, "number": module.number},
            "week": {"number": week.number, "start_date": week.start_date,
                     "end_date": week.end_date, "parity": week.parity},
            "total_weeks": len(weeks),
            "run_id": run.pk if run else None,
            "days": [{**day,
                      "date": week.monday
                              + dt.timedelta(days=day["day_of_week"])}
                     for day in matrix["days"]],
        }
        for day in payload["days"]:
            for slot in day["slots"]:
                for cell in slot["classes"]:
                    cell["date"] = day["date"]
        return Response(CalendarGridSerializer(payload).data)


# ---------------------------------------------------------------------------
#day (мобильный вид «расписание на день»)
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(
        summary="[Фронт] Расписание носителя на один день",
        description="Пары конкретного календарного дня: дата определяется "
                    "неделей модуля и чётностью шаблона недели.",
        tags=["Frontend"],
        parameters=[
            OpenApiParameter("date", dt.date, required=True,
                             description="Дата (ГГГГ-ММ-ДД)."),
            OpenApiParameter("holder_type", str, required=True,
                             description=f"Носитель: {'|'.join(HOLDER_TYPES)}."),
            OpenApiParameter("holder_id", int, required=True),
            OpenApiParameter("semester", int, required=True),
            OpenApiParameter("run", int, required=False,
                             description="ID запуска; по умолчанию — последний успешный."),
        ],
        responses=DayScheduleSerializer,
    ),
)
class DayScheduleViewSet(viewsets.ViewSet):
    """GET /api/schedule/frontend/day/?date=...&holder_type=...&holder_id=..."""

    permission_classes: list = []

    def list(self, request):
        params = request.query_params
        date_raw = params.get("date")
        holder_type = params.get("holder_type")
        holder_id = params.get("holder_id")
        semester_id = params.get("semester")
        if not (date_raw and holder_type and holder_id and semester_id):
            return Response({"detail": "Обязательны date, holder_type, "
                                       "holder_id, semester."}, status=400)
        try:
            target = dt.date.fromisoformat(date_raw)
        except ValueError:
            return Response({"detail": "Некорректная дата."}, status=400)
        semester = Semester.objects.filter(pk=semester_id).first()
        if semester is None:
            return Response({"detail": "Семестр не найден."}, status=404)

        pattern = None
        for module in semester.modules.order_by("number"):
            for week in build_calendar_weeks(module):
                if week.start_date <= target <= week.end_date:
                    pattern = resolve_pattern_for_date(semester, week)
                    break
            if pattern is not None:
                break
        if pattern is None:
            return Response({"detail": "Дата вне учебных недель модулей "
                                       "этого семестра."}, status=404)

        run_id = params.get("run")
        run = (GenerationRun.objects.filter(pk=run_id).first() if run_id
               else _latest_run(semester))
        cells = []
        if run is not None:
            day_of_week = target.weekday()
            classes = (c for c in _load_classes(run, _holder_filter(holder_type,
                                                                    int(holder_id)),
                                                week_pattern_id=pattern.pk)
                       if c.slot.day_of_week == day_of_week)
            for sc in sorted(classes, key=lambda c: c.slot.lesson_number):
                cell = _class_cell(sc)
                cell.update({
                    "lesson_number": sc.slot.lesson_number,
                    "start_time": sc.slot.start_time,
                    "end_time": sc.slot.end_time,
                })
                cells.append(cell)
        return Response({
            "date": target,
            "day_name": DAY_NAMES.get(target.weekday(), ""),
            "week_pattern": pattern.pk,
            "run_id": run.pk if run else None,
            "classes": cells,
        })


# ---------------------------------------------------------------------------
#me (карточка студента/преподавателя)
# ---------------------------------------------------------------------------

@extend_schema_view(
    retrieve=extend_schema(
        summary="[Фронт] Моё расписание: студент",
        description="Карточка студента: ФИО, группа, выбранный проект ПД и "
                    "недельная сетка (пары группы + потоковые лекции + ПД). "
                    "Физкультура возвращается отдельной пометкой — она не "
                    "планируется решателем.",
        tags=["Frontend"],
        parameters=[
            OpenApiParameter("id", int, location="path", description="ID студента."),
            OpenApiParameter("semester", int, required=False,
                             description="ID семестра; по умолчанию — последний."),
            OpenApiParameter("pattern", int, required=False,
                             description="ID шаблона недели."),
            OpenApiParameter("run", int, required=False,
                             description="ID запуска; по умолчанию — последний успешный."),
        ],
        responses=MeResponseSerializer,
    ),
)
class StudentMeViewSet(viewsets.ViewSet):
    """GET /api/schedule/frontend/me/students/<id>/?semester=..."""

    permission_classes: list = []

    def retrieve(self, request, pk=None):
        student = (Student.objects.select_related("group", "project_choice",
                                                  "project_choice__project")
                   .filter(pk=pk).first())
        if student is None:
            return Response({"detail": "Студент не найден."}, status=404)
        semester_id = request.query_params.get("semester")
        semester = (Semester.objects.filter(pk=semester_id).first() if semester_id
                    else Semester.objects.order_by("-start_date").first())
        if semester is None:
            return Response({"detail": "Семестр не задан и не найден."}, status=400)

        profile = {
            "full_name": student.full_name,
            "group": student.group.name if student.group_id else "",
            "project": (student.project_choice.project.title
                        if student.project_choice_id else ""),
        }
        grid = self._grid(student, semester, request)
        pe_events = []
        for ev in (LessonEvent.objects
                   .filter(semester=semester, lesson_type=LessonType.PE)
                   .filter(Q(group=student.group) | Q(stream__groups=student.group))
                   .select_related("subject")):
            pe_events.append({"subject": ev.subject.name,
                              "pairs_per_week": ev.pairs_per_week})
        return Response({"holder_type": "student", "profile": profile,
                        **grid, "pe_notes": pe_events})

    def _grid(self, student: Student, semester: Semester, request) -> dict:
        run_id = request.query_params.get("run")
        run = (GenerationRun.objects.filter(pk=run_id).first() if run_id
               else _latest_run(semester))
        pattern_id = request.query_params.get("pattern")
        pattern = (WeekPattern.objects.filter(pk=pattern_id).first() if pattern_id
                   else WeekPattern.objects.filter(semester=semester)
                        .order_by("id").first())
        if run is None or pattern is None:
            return {"run_id": run.pk if run else None,
                    "week_pattern": None, "days": []}
        holder_id = student.pk
        classes = _load_classes(run, _holder_filter("student", holder_id),
                                week_pattern_id=pattern.pk)
        return {"run_id": run.pk, **_build_matrix(pattern, classes)}


@extend_schema_view(
    retrieve=extend_schema(
        summary="[Фронт] Моё расписание: преподаватель",
        description="Карточка преподавателя: ФИО, суммарная нагрузка "
                    "(пар в неделю по сетке) и недельная сетка его занятий.",
        tags=["Frontend"],
        parameters=[
            OpenApiParameter("id", int, location="path", description="ID преподавателя."),
            OpenApiParameter("semester", int, required=False,
                             description="ID семестра; по умолчанию — последний."),
            OpenApiParameter("pattern", int, required=False,
                             description="ID шаблона недели."),
            OpenApiParameter("run", int, required=False,
                             description="ID запуска; по умолчанию — последний успешный."),
        ],
        responses=MeResponseSerializer,
    ),
)
class TeacherMeViewSet(viewsets.ViewSet):
    """GET /api/schedule/frontend/me/teachers/<id>/?semester=..."""

    permission_classes: list = []

    def retrieve(self, request, pk=None):
        teacher = Teacher.objects.filter(pk=pk).first()
        if teacher is None:
            return Response({"detail": "Преподаватель не найден."}, status=404)
        semester_id = request.query_params.get("semester")
        semester = (Semester.objects.filter(pk=semester_id).first() if semester_id
                    else Semester.objects.order_by("-start_date").first())
        if semester is None:
            return Response({"detail": "Семестр не задан и не найден."}, status=400)

        run_id = request.query_params.get("run")
        run = (GenerationRun.objects.filter(pk=run_id).first() if run_id
               else _latest_run(semester))
        pattern_id = request.query_params.get("pattern")
        pattern = (WeekPattern.objects.filter(pk=pattern_id).first() if pattern_id
                   else WeekPattern.objects.filter(semester=semester)
                        .order_by("id").first())
        grid: dict = {"run_id": run.pk if run else None,
                      "week_pattern": None, "days": []}
        load = 0
        if run is not None and pattern is not None:
            classes = _load_classes(run, Q(event__teacher_id=teacher.pk),
                                    week_pattern_id=pattern.pk)
            load = len(classes)
            grid = {"run_id": run.pk, **_build_matrix(pattern, classes)}
        return Response({
            "holder_type": "teacher",
            "profile": {"full_name": teacher.full_name},
            "weekly_load": load,
            "limits": {"max_pairs_per_day": teacher.max_pairs_per_day,
                       "max_pairs_per_week": teacher.max_pairs_per_week},
            **grid,
        })
