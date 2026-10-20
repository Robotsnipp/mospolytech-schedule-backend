"""HTTP-представления фичи «генерация расписания» (DRF ViewSets).

Здесь ТОЛЬКО протокол: разбор/валидация запроса -> вызов сервисного слоя
(services/generation.run_generation) -> сериализация ответа. Никакой
бизнес-логики и работы с решателем — всё это видно в services/.

Группы эндпоинтов (маршрутизация через DefaultRouter, см. urls.py):

1. CRUD справочников (ModelViewSet) — данные для генерации:
   semesters, week-patterns, time-slots, modules, groups, students,
   teachers, subjects, locations, buildings, rooms, streams, curricula,
   curriculum-entries, projects, project-enrollments, events.
2. Read-only результаты (ReadOnlyModelViewSet) — то, что создаёт решатель:
   runs (+ actions classes/issues), scheduled-classes, issues.
3. Потребление расписания (только чтение, фильтр по носителю):
   GET /api/schedule/schedule/groups/<id>/?semester=...
   GET /api/schedule/schedule/streams/<id>/?semester=...
   GET /api/schedule/schedule/teachers/<id>/?semester=...
4. Запуск генерации: POST /api/schedule/generate/.

Все публичные методы annotated @extend_schema — схема Swagger содержит
русские описания, параметры фильтрации и схемы запросов/ответов.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.response import Response

from ..models import (
    Building,
    Curriculum,
    CurriculumEntry,
    GenerationIssue,
    GenerationRun,
    Group,
    LectureStream,
    LessonEvent,
    Location,
    Module,
    Project,
    ProjectEnrollment,
    Room,
    ScheduledClass,
    Semester,
    Student,
    Subject,
    Teacher,
    TimeSlot,
    WeekPattern,
)
from ..services import GenerationError, run_generation
from ..services.curriculum import (
    CurriculumDataError,
    import_curriculum,
    prepare_module_load,
)
from ..services.schedule_read import (
    read_schedule,
    resolve_schedule_run,
    with_schedule_details,
)
from .preparation import (
    CurriculumImportRequestSerializer,
    CurriculumImportResultSerializer,
    ModuleLoadRequestSerializer,
    ModuleLoadResultSerializer,
)
from .schedule import ScheduleResponseSerializer
from .serializers import (
    BuildingSerializer,
    CurriculumEntrySerializer,
    CurriculumSerializer,
    GenerateRequestSerializer,
    GenerationIssueSerializer,
    GenerationRunListSerializer,
    GenerationRunSerializer,
    GroupSerializer,
    LectureStreamSerializer,
    LessonEventSerializer,
    LocationSerializer,
    ModuleSerializer,
    PatternScheduleFilterSerializer,
    ProjectEnrollmentSerializer,
    ProjectSerializer,
    RoomSerializer,
    ScheduledClassSerializer,
    ScheduleFilterSerializer,
    SemesterSerializer,
    StudentSerializer,
    SubjectSerializer,
    TeacherSerializer,
    TimeSlotSerializer,
    WeekPatternSerializer,
)

SEMESTER_PARAM = OpenApiParameter(
    "semester", int, description="ID семестра (обязателен).")
MODULE_PARAM = OpenApiParameter(
    "module", int, required=False, description="ID календарного модуля.")


class CrudViewSet(viewsets.ModelViewSet):
    """Базовый CRUD viewset: список/детка/создание/изменение/удаление.

    Права настраиваются глобально (DRF DEFAULT_PERMISSION_CLASSES); здесь
    только общий порядок list (по id) и подключённые фильтры поиска.
    """

    search_fields: list[str] = []
    ordering: list[str] = ["id"]

    def get_queryset(self):
        return self.queryset.all().order_by(*self.ordering)


# ---------------------------------------------------------------------------
# Справочники: календарь
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(summary="Список семестров", tags=["Календарь"]),
    retrieve=extend_schema(summary="Семестр по ID", tags=["Календарь"]),
    create=extend_schema(summary="Создать семестр", tags=["Календарь"]),
    update=extend_schema(summary="Обновить семестр (PUT)", tags=["Календарь"]),
    partial_update=extend_schema(summary="Частично обновить семестр (PATCH)",
                                 tags=["Календарь"]),
    destroy=extend_schema(summary="Удалить семестр", tags=["Календарь"]),
)
class SemesterViewSet(CrudViewSet):
    """Семестры: учебные периоды с датой начала и числом недель."""

    queryset = Semester.objects.all()
    serializer_class = SemesterSerializer
    search_fields = ["name"]


@extend_schema_view(
    list=extend_schema(summary="Список шаблонов недели", tags=["Календарь"],
                       parameters=[OpenApiParameter("semester", int, required=False,
                                                    description="Фильтр по семестру.")]),
    retrieve=extend_schema(summary="Шаблон недели по ID", tags=["Календарь"]),
    create=extend_schema(summary="Создать шаблон недели", tags=["Календарь"]),
    update=extend_schema(summary="Обновить шаблон недели (PUT)", tags=["Календарь"]),
    partial_update=extend_schema(summary="Частично обновить шаблон (PATCH)",
                                 tags=["Календарь"]),
    destroy=extend_schema(summary="Удалить шаблон недели", tags=["Календарь"]),
)
class WeekPatternViewSet(CrudViewSet):
    """Шаблоны недели (сетки пар) семестра; чётная/нечётная — разные паттерны."""

    queryset = WeekPattern.objects.select_related("semester")
    serializer_class = WeekPatternSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        semester = self.request.query_params.get("semester")
        if semester:
            qs = qs.filter(semester_id=semester)
        return qs


@extend_schema_view(
    list=extend_schema(summary="Список слотов (пар сетки)", tags=["Календарь"],
                       parameters=[OpenApiParameter("week_pattern", int, required=False,
                                                    description="Фильтр по шаблону недели.")]),
    retrieve=extend_schema(summary="Слот по ID", tags=["Календарь"]),
    create=extend_schema(summary="Создать слот", tags=["Календарь"]),
    update=extend_schema(summary="Обновить слот (PUT)", tags=["Календарь"]),
    partial_update=extend_schema(summary="Частично обновить слот (PATCH)",
                                 tags=["Календарь"]),
    destroy=extend_schema(summary="Удалить слот", tags=["Календарь"]),
)
class TimeSlotViewSet(CrudViewSet):
    """Слоты недельной сетки: день недели + номер пары + время."""

    queryset = TimeSlot.objects.select_related("week_pattern")
    serializer_class = TimeSlotSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        pattern = self.request.query_params.get("week_pattern")
        if pattern:
            qs = qs.filter(week_pattern_id=pattern)
        return qs


@extend_schema_view(
    list=extend_schema(summary="Список модулей", tags=["Календарь"],
                       parameters=[OpenApiParameter("semester", int, required=False,
                                                    description="Фильтр по семестру.")]),
    retrieve=extend_schema(summary="Модуль по ID", tags=["Календарь"]),
    create=extend_schema(summary="Создать модуль", tags=["Календарь"]),
    update=extend_schema(summary="Обновить модуль (PUT)", tags=["Календарь"]),
    partial_update=extend_schema(summary="Частично обновить модуль (PATCH)",
                                 tags=["Календарь"]),
    destroy=extend_schema(summary="Удалить модуль", tags=["Календарь"]),
)
class ModuleViewSet(CrudViewSet):
    """Календарные модули: даты и число учебных недель внутри семестра."""

    queryset = Module.objects.select_related("semester")
    serializer_class = ModuleSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        semester = self.request.query_params.get("semester")
        if semester:
            qs = qs.filter(semester_id=semester)
        return qs


# ---------------------------------------------------------------------------
# Справочники: участники
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(summary="Список групп", tags=["Участники"],
                       parameters=[
                           OpenApiParameter("semester", int, required=False,
                                            description="Фильтр по семестру."),
                           OpenApiParameter("is_dpo", bool, required=False,
                                            description="Только группы ДПО / обычные."),
                       ]),
    retrieve=extend_schema(summary="Группа по ID", tags=["Участники"]),
    create=extend_schema(summary="Создать группу", tags=["Участники"]),
    update=extend_schema(summary="Обновить группу (PUT)", tags=["Участники"]),
    partial_update=extend_schema(summary="Частично обновить группу (PATCH)",
                                 tags=["Участники"]),
    destroy=extend_schema(summary="Удалить группу", tags=["Участники"]),
)
class GroupViewSet(CrudViewSet):
    """Учебные группы и группы ДПО."""

    queryset = Group.objects.select_related("semester", "curriculum") \
        .annotate(students_count=Count("students"))
    serializer_class = GroupSerializer
    search_fields = ["name"]

    @extend_schema(summary="Подготовить нагрузку группы в модуле", tags=["Учебные планы"],
                   request=ModuleLoadRequestSerializer, responses={200: ModuleLoadResultSerializer})
    @action(detail=True, methods=["post"], url_path="prepare-load")
    def prepare_load(self, request, pk=None):
        group = self.get_object()
        serializer = ModuleLoadRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = prepare_module_load(group=group, **serializer.validated_data)
        except (CurriculumDataError, ValidationError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get("semester"):
            qs = qs.filter(semester_id=params["semester"])
        if params.get("is_dpo"):
            qs = qs.filter(is_dpo=params["is_dpo"].lower() in ("1", "true"))
        return qs


@extend_schema_view(
    list=extend_schema(summary="Список студентов", tags=["Участники"],
                       parameters=[OpenApiParameter("group", int, required=False,
                                                    description="Фильтр по учебной группе.")]),
    retrieve=extend_schema(summary="Студент по ID", tags=["Участники"]),
    create=extend_schema(summary="Создать студента", tags=["Участники"]),
    update=extend_schema(summary="Обновить студента (PUT)", tags=["Участники"]),
    partial_update=extend_schema(summary="Частично обновить студента (PATCH)",
                                 tags=["Участники"]),
    destroy=extend_schema(summary="Удалить студента", tags=["Участники"]),
)
class StudentViewSet(CrudViewSet):
    """Студенты (слушатели) учебных групп."""

    queryset = Student.objects.select_related("group")
    serializer_class = StudentSerializer
    search_fields = ["full_name"]

    def get_queryset(self):
        qs = super().get_queryset()
        group = self.request.query_params.get("group")
        if group:
            qs = qs.filter(group_id=group)
        return qs


@extend_schema_view(
    list=extend_schema(summary="Список преподавателей", tags=["Участники"]),
    retrieve=extend_schema(summary="Преподаватель по ID", tags=["Участники"]),
    create=extend_schema(summary="Создать преподавателя", tags=["Участники"]),
    update=extend_schema(summary="Обновить преподавателя (PUT)", tags=["Участники"]),
    partial_update=extend_schema(summary="Частично обновить преподавателя (PATCH)",
                                 tags=["Участники"]),
    destroy=extend_schema(summary="Удалить преподавателя", tags=["Участники"]),
)
class TeacherViewSet(CrudViewSet):
    """Преподаватели с лимитами нагрузки (пар в день/неделю)."""

    queryset = Teacher.objects.all()
    serializer_class = TeacherSerializer
    search_fields = ["full_name"]


# ---------------------------------------------------------------------------
# Справочники: ресурсы
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(summary="Список дисциплин", tags=["Ресурсы"]),
    retrieve=extend_schema(summary="Дисциплина по ID", tags=["Ресурсы"]),
    create=extend_schema(summary="Создать дисциплину", tags=["Ресурсы"]),
    update=extend_schema(summary="Обновить дисциплину (PUT)", tags=["Ресурсы"]),
    partial_update=extend_schema(summary="Частично обновить дисциплину (PATCH)",
                                 tags=["Ресурсы"]),
    destroy=extend_schema(summary="Удалить дисциплину", tags=["Ресурсы"]),
)
class SubjectViewSet(CrudViewSet):
    """Дисциплины (предметы)."""

    queryset = Subject.objects.all()
    serializer_class = SubjectSerializer
    search_fields = ["name"]


@extend_schema_view(
    list=extend_schema(summary="Список локаций", tags=["Ресурсы"]),
    retrieve=extend_schema(summary="Локация по ID", tags=["Ресурсы"]),
    create=extend_schema(summary="Создать локацию", tags=["Ресурсы"]),
    update=extend_schema(summary="Обновить локацию (PUT)", tags=["Ресурсы"]),
    partial_update=extend_schema(summary="Частично обновить локацию (PATCH)",
                                 tags=["Ресурсы"]),
    destroy=extend_schema(summary="Удалить локацию", tags=["Ресурсы"]),
)
class LocationViewSet(CrudViewSet):
    """Учебные локации (адреса; между локациями — час езды)."""

    queryset = Location.objects.all()
    serializer_class = LocationSerializer
    search_fields = ["name", "address"]


@extend_schema_view(
    list=extend_schema(summary="Список корпусов", tags=["Ресурсы"],
                       parameters=[OpenApiParameter("location", int, required=False,
                                                    description="Фильтр по локации.")]),
    retrieve=extend_schema(summary="Корпус по ID", tags=["Ресурсы"]),
    create=extend_schema(summary="Создать корпус", tags=["Ресурсы"]),
    update=extend_schema(summary="Обновить корпус (PUT)", tags=["Ресурсы"]),
    partial_update=extend_schema(summary="Частично обновить корпус (PATCH)",
                                 tags=["Ресурсы"]),
    destroy=extend_schema(summary="Удалить корпус", tags=["Ресурсы"]),
)
class BuildingViewSet(CrudViewSet):
    """Учебные корпуса на локациях."""

    queryset = Building.objects.select_related("location")
    serializer_class = BuildingSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        location = self.request.query_params.get("location")
        if location:
            qs = qs.filter(location_id=location)
        return qs


@extend_schema_view(
    list=extend_schema(summary="Список аудиторий", tags=["Ресурсы"],
                       parameters=[
                           OpenApiParameter("building", int, required=False,
                                            description="Фильтр по корпусу."),
                           OpenApiParameter("is_active", bool, required=False,
                                            description="Только доступные для генерации."),
                       ]),
    retrieve=extend_schema(summary="Аудитория по ID", tags=["Ресурсы"]),
    create=extend_schema(summary="Создать аудиторию", tags=["Ресурсы"]),
    update=extend_schema(summary="Обновить аудиторию (PUT)", tags=["Ресурсы"]),
    partial_update=extend_schema(summary="Частично обновить аудиторию (PATCH)",
                                 tags=["Ресурсы"]),
    destroy=extend_schema(summary="Удалить аудиторию", tags=["Ресурсы"]),
)
class RoomViewSet(CrudViewSet):
    """Аудитории: вместимость, оборудование, доступность для генерации."""

    queryset = Room.objects.select_related("building", "building__location")
    serializer_class = RoomSerializer
    search_fields = ["name"]

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get("building"):
            qs = qs.filter(building_id=params["building"])
        if params.get("is_active"):
            qs = qs.filter(is_active=params["is_active"].lower() in ("1", "true"))
        return qs


@extend_schema_view(
    list=extend_schema(summary="Список лекционных потоков", tags=["Ресурсы"],
                       parameters=[OpenApiParameter("semester", int, required=False,
                                                    description="Фильтр по семестру.")]),
    retrieve=extend_schema(summary="Лекционный поток по ID", tags=["Ресурсы"]),
    create=extend_schema(summary="Создать лекционный поток", tags=["Ресурсы"]),
    update=extend_schema(summary="Обновить лекционный поток (PUT)", tags=["Ресурсы"]),
    partial_update=extend_schema(summary="Частично обновить поток (PATCH)",
                                 tags=["Ресурсы"]),
    destroy=extend_schema(summary="Удалить лекционный поток", tags=["Ресурсы"]),
)
class LectureStreamViewSet(CrudViewSet):
    """Лекционные потоки: набор групп, совместно слушающих одну онлайн-лекцию."""

    queryset = (LectureStream.objects
                .select_related("semester", "subject", "teacher")
                .prefetch_related("groups"))
    serializer_class = LectureStreamSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        semester = self.request.query_params.get("semester")
        if semester:
            qs = qs.filter(semester_id=semester)
        return qs


# ---------------------------------------------------------------------------
# Учебные планы
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(summary="Список учебных планов", tags=["Учебные планы"],
                       parameters=[
                           OpenApiParameter("admission_year", int, required=False,
                                            description="Фильтр по году набора."),
                           OpenApiParameter("study_form", str, required=False,
                                            description="Фильтр по форме обучения "
                                                        "(FULL_TIME/PART_TIME/EXTRAMURAL)."),
                       ]),
    retrieve=extend_schema(summary="Учебный план по ID", tags=["Учебные планы"]),
    create=extend_schema(summary="Создать учебный план", tags=["Учебные планы"]),
    update=extend_schema(summary="Обновить учебный план (PUT)", tags=["Учебные планы"]),
    partial_update=extend_schema(summary="Частично обновить план (PATCH)",
                                 tags=["Учебные планы"]),
    destroy=extend_schema(summary="Удалить учебный план", tags=["Учебные планы"]),
)
class CurriculumViewSet(CrudViewSet):
    """Учебные планы программ обучения."""

    queryset = Curriculum.objects.all()
    serializer_class = CurriculumSerializer
    search_fields = ["name", "program_code", "profile"]

    @extend_schema(summary="Импортировать проверенный JSON учебного плана", tags=["Учебные планы"],
                   request=CurriculumImportRequestSerializer, responses={200: CurriculumImportResultSerializer})
    @action(detail=False, methods=["post"], url_path="import")
    def import_plan(self, request):
        serializer = CurriculumImportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = import_curriculum(**serializer.validated_data)
        except (CurriculumDataError, ValidationError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get("admission_year"):
            qs = qs.filter(admission_year=params["admission_year"])
        if params.get("study_form"):
            qs = qs.filter(study_form=params["study_form"])
        return qs


@extend_schema_view(
    list=extend_schema(summary="Список строк нагрузки планов", tags=["Учебные планы"],
                       parameters=[OpenApiParameter("curriculum", int, required=False,
                                                    description="Фильтр по учебному плану.")]),
    retrieve=extend_schema(summary="Строка плана по ID", tags=["Учебные планы"]),
    create=extend_schema(summary="Создать строку нагрузки плана", tags=["Учебные планы"]),
    update=extend_schema(summary="Обновить строку плана (PUT)", tags=["Учебные планы"]),
    partial_update=extend_schema(summary="Частично обновить строку плана (PATCH)",
                                 tags=["Учебные планы"]),
    destroy=extend_schema(summary="Удалить строку плана", tags=["Учебные планы"]),
)
class CurriculumEntryViewSet(CrudViewSet):
    """Семестровые нагрузки: часы дисциплины в конкретном семестре обучения."""

    queryset = CurriculumEntry.objects.select_related("curriculum", "subject")
    serializer_class = CurriculumEntrySerializer

    def get_queryset(self):
        qs = super().get_queryset()
        curriculum = self.request.query_params.get("curriculum")
        if curriculum:
            qs = qs.filter(curriculum_id=curriculum)
        return qs


# ---------------------------------------------------------------------------
# Проектная деятельность
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(summary="Список проектов ПД", tags=["Проектная деятельность"],
                       parameters=[OpenApiParameter("semester", int, required=False,
                                                    description="Фильтр по семестру.")]),
    retrieve=extend_schema(summary="Проект ПД по ID", tags=["Проектная деятельность"]),
    create=extend_schema(summary="Создать проект ПД", tags=["Проектная деятельность"]),
    update=extend_schema(summary="Обновить проект ПД (PUT)",
                         tags=["Проектная деятельность"]),
    partial_update=extend_schema(summary="Частично обновить проект ПД (PATCH)",
                                 tags=["Проектная деятельность"]),
    destroy=extend_schema(summary="Удалить проект ПД", tags=["Проектная деятельность"]),
)
class ProjectViewSet(CrudViewSet):
    """Проекты проектной деятельности (ПД)."""

    queryset = (Project.objects.select_related("semester", "supervisor")
                .annotate(enrolled_count=Count("enrollments")))
    serializer_class = ProjectSerializer
    search_fields = ["title"]

    def get_queryset(self):
        qs = super().get_queryset()
        semester = self.request.query_params.get("semester")
        if semester:
            qs = qs.filter(semester_id=semester)
        return qs


@extend_schema_view(
    list=extend_schema(summary="Список выборов проектов", tags=["Проектная деятельность"],
                       parameters=[
                           OpenApiParameter("project", int, required=False,
                                            description="Фильтр по проекту."),
                           OpenApiParameter("student", int, required=False,
                                            description="Фильтр по студенту."),
                       ]),
    retrieve=extend_schema(summary="Выбор проекта по ID", tags=["Проектная деятельность"]),
    create=extend_schema(summary="Записать студента на проект",
                         tags=["Проектная деятельность"]),
    update=extend_schema(summary="Обновить выбор проекта (PUT)",
                         tags=["Проектная деятельность"]),
    partial_update=extend_schema(summary="Частично обновить выбор проекта (PATCH)",
                                 tags=["Проектная деятельность"]),
    destroy=extend_schema(summary="Удалить выбор проекта", tags=["Проектная деятельность"]),
)
class ProjectEnrollmentViewSet(CrudViewSet):
    """Выборы проектов студентами («группа ПД» = все выбравшие один проект)."""

    queryset = ProjectEnrollment.objects.select_related("student", "student__group",
                                                        "project")
    serializer_class = ProjectEnrollmentSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get("project"):
            qs = qs.filter(project_id=params["project"])
        if params.get("student"):
            qs = qs.filter(student_id=params["student"])
        return qs


# ---------------------------------------------------------------------------
# Занятия (план) — CRUD; результат генерации — read-only
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(summary="Список занятий к планированию", tags=["Занятия"],
                       parameters=[
                           SEMESTER_PARAM,
                           OpenApiParameter("module", int, required=False,
                                            description="Фильтр по модулю."),
                           OpenApiParameter("group", int, required=False,
                                            description="Фильтр по группе."),
                           OpenApiParameter("stream", int, required=False,
                                            description="Фильтр по лекционному потоку."),
                           OpenApiParameter("project", int, required=False,
                                            description="Фильтр по проекту ПД."),
                           OpenApiParameter("lesson_type", str, required=False,
                                            description="Фильтр по типу занятия."),
                       ]),
    retrieve=extend_schema(summary="Занятие по ID", tags=["Занятия"]),
    create=extend_schema(summary="Создать занятие (потребность)", tags=["Занятия"]),
    update=extend_schema(summary="Обновить занятие (PUT)", tags=["Занятия"]),
    partial_update=extend_schema(summary="Частично обновить занятие (PATCH)",
                                 tags=["Занятия"]),
    destroy=extend_schema(summary="Удалить занятие", tags=["Занятия"]),
)
class LessonEventViewSet(CrudViewSet):
    """LessonEvent — «что нужно запланировать»: тип, носитель, пар в неделю."""

    queryset = (LessonEvent.objects
                .select_related("semester", "subject", "group", "stream",
                                "project", "teacher", "module"))
    serializer_class = LessonEventSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        for field in ("semester", "module", "group", "stream", "project",
                      "lesson_type"):
            value = params.get(field)
            if value:
                lookup = f"{field}_id" if field not in ("lesson_type",) else field
                qs = qs.filter(**{lookup: value})
        return qs


@extend_schema_view(
    list=extend_schema(summary="Список пар расписания", tags=["Расписание"],
                       parameters=[
                           OpenApiParameter("run", int, required=False,
                                            description="Фильтр по запуску генерации."),
                           OpenApiParameter("event", int, required=False,
                                            description="Фильтр по занятию."),
                           OpenApiParameter("slot", int, required=False,
                                            description="Фильтр по слоту."),
                           OpenApiParameter("room", int, required=False,
                                            description="Фильтр по аудитории."),
                       ]),
    retrieve=extend_schema(summary="Пара расписания по ID", tags=["Расписание"]),
)
class ScheduledClassViewSet(viewsets.ReadOnlyModelViewSet):
    """ScheduledClass — результат генерации: пара в слот и аудиторию."""

    queryset = with_schedule_details(ScheduledClass.objects.all())
    serializer_class = ScheduledClassSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        for field in ("run", "event", "slot", "room"):
            value = params.get(field)
            if value:
                qs = qs.filter(**{f"{field}_id": value})
        return qs


# ---------------------------------------------------------------------------
# Журнал генерации
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(summary="История запусков генерации", tags=["Генерация"],
                       parameters=[SEMESTER_PARAM, MODULE_PARAM]),
    retrieve=extend_schema(summary="Запуск генерации с полным результатом",
                           tags=["Генерация"]),
)
class GenerationRunViewSet(viewsets.ReadOnlyModelViewSet):
    """Запуски решателя: список — компактный, детка — со всеми парами и проблемами."""

    queryset = GenerationRun.objects.select_related("semester", "module")
    serializer_class = GenerationRunSerializer

    def get_serializer_class(self):
        if self.action == "list":
            return GenerationRunListSerializer
        return GenerationRunSerializer

    def get_queryset(self):
        qs = (super().get_queryset()
              .annotate(classes_count=Count("scheduled_classes", distinct=True),
                        issues_count=Count("issues", distinct=True))
              .order_by("-created_at", "-pk"))
        filters = ScheduleFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        for key in ("semester", "module"):
            if value := filters.validated_data.get(key):
                qs = qs.filter(**{key: value})
        if self.action == "retrieve":
            qs = qs.prefetch_related(
                Prefetch("scheduled_classes", queryset=with_schedule_details(ScheduledClass.objects.all())),
                "issues")
        return qs

    @extend_schema(
        summary="Пари конкретного запуска", tags=["Генерация"],
        responses=ScheduledClassSerializer(many=True),
    )
    @action(detail=True, methods=["get"], url_path="classes")
    def classes(self, request, pk=None):
        run = self.get_object()
        classes = with_schedule_details(run.scheduled_classes.all())
        return Response(ScheduledClassSerializer(classes, many=True).data)

    @extend_schema(
        summary="Проблемы конкретного запуска", tags=["Генерация"],
        responses=GenerationIssueSerializer(many=True),
    )
    @action(detail=True, methods=["get"], url_path="issues")
    def issues(self, request, pk=None):
        run = self.get_object()
        issues = run.issues.select_related("event")
        return Response(GenerationIssueSerializer(issues, many=True).data)


@extend_schema_view(
    list=extend_schema(summary="Список проблем генерации", tags=["Генерация"],
                       parameters=[OpenApiParameter("run", int, required=False,
                                                    description="Фильтр по запуску."),
                                   OpenApiParameter("level", str, required=False,
                                                    description="WARNING / ERROR.")]),
    retrieve=extend_schema(summary="Проблема генерации по ID", tags=["Генерация"]),
)
class GenerationIssueViewSet(viewsets.ReadOnlyModelViewSet):
    """Предупреждения и ошибки процесса генерации (например, unscheduled event)."""

    queryset = GenerationIssue.objects.select_related("run", "event")
    serializer_class = GenerationIssueSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get("run"):
            qs = qs.filter(run_id=params["run"])
        if params.get("level"):
            qs = qs.filter(level=params["level"])
        return qs


# ---------------------------------------------------------------------------
# Просмотр расписания потребителями (группа / поток / преподаватель)
# ---------------------------------------------------------------------------

class ScheduleReadViewSet(viewsets.ViewSet):
    """Один запуск: общие пары и подходящая носителю фиксированная ПД."""

    permission_classes: list = []
    lookup_value_regex = r"\d+"

    def _list_schedule(self, kind="all", holder=None):
        serializer = PatternScheduleFilterSerializer(data=self.request.query_params)
        serializer.is_valid(raise_exception=True)
        try:
            run = resolve_schedule_run(**serializer.validated_data)
        except GenerationRun.DoesNotExist:
            return Response({"detail": "Запуск генерации не найден."}, status=status.HTTP_404_NOT_FOUND)
        except ValidationError as exc:
            raise APIValidationError(exc.message_dict) from exc
        result = read_schedule(run=run, kind=kind, holder=holder,
                               week_pattern=serializer.validated_data.get("week_pattern"))
        return Response(ScheduleResponseSerializer(
            result, context={"holder_kind": kind, "holder": holder}).data)


@extend_schema_view(
    list=extend_schema(operation_id="schedule_all_classes",
                       summary="Все занятия одного запуска", tags=["Расписание"],
                       parameters=[PatternScheduleFilterSerializer], responses=ScheduleResponseSerializer),
    retrieve=extend_schema(summary="Расписание группы с ПД её участников", tags=["Расписание"],
                           parameters=[PatternScheduleFilterSerializer,
                                       OpenApiParameter("id", int, location=OpenApiParameter.PATH)],
                           responses=ScheduleResponseSerializer),
)
class GroupScheduleViewSet(ScheduleReadViewSet):
    """ПД группы относится только к перечисленным студентам, не ко всей группе."""

    def list(self, request):
        return self._list_schedule()

    def retrieve(self, request, pk=None):
        return self._list_schedule("group", get_object_or_404(Group, pk=pk))


@extend_schema_view(
    retrieve=extend_schema(summary="Расписание лекционного потока", tags=["Расписание"],
                           parameters=[PatternScheduleFilterSerializer,
                                       OpenApiParameter("id", int, location=OpenApiParameter.PATH)],
                           responses=ScheduleResponseSerializer),
)
class StreamScheduleViewSet(ScheduleReadViewSet):
    def retrieve(self, request, pk=None):
        return self._list_schedule("stream", get_object_or_404(LectureStream, pk=pk))


@extend_schema_view(
    retrieve=extend_schema(summary="Расписание преподавателя, включая ПД", tags=["Расписание"],
                           parameters=[PatternScheduleFilterSerializer,
                                       OpenApiParameter("id", int, location=OpenApiParameter.PATH)],
                           responses=ScheduleResponseSerializer),
)
class TeacherScheduleViewSet(ScheduleReadViewSet):
    def retrieve(self, request, pk=None):
        return self._list_schedule("teacher", get_object_or_404(Teacher, pk=pk))


@extend_schema_view(
    retrieve=extend_schema(summary="Расписание студента, включая его ПД", tags=["Расписание"],
                           parameters=[PatternScheduleFilterSerializer,
                                       OpenApiParameter("id", int, location=OpenApiParameter.PATH)],
                           responses=ScheduleResponseSerializer),
)
class StudentScheduleViewSet(ScheduleReadViewSet):
    def retrieve(self, request, pk=None):
        student = get_object_or_404(Student.objects.select_related("group"), pk=pk)
        return self._list_schedule("student", student)


# ---------------------------------------------------------------------------
# Запуск генерации
# ---------------------------------------------------------------------------

class GenerateViewSet(viewsets.ViewSet):
    """Запуск генерации расписания (OR-Tools CP-SAT).

    POST /api/schedule/generate/ (action generate, см. url_path ниже).

    Что происходит внутри (детали — в services/generation.py):
    1. Берутся все LessonEvent семестра; физкультура (PE) исключается —
       она не генерируется (студент приходит в любое время/место).
    2. Лекции планируются онлайн на потоки групп (число пар задано нагрузкой),
       практики/лаборатории — очно на группу, ПД — очно на студентов,
       выбравших проект, внеучебка — очно или онлайн (поле format).
    3. Фиксированная ПД блокирует время участников и преподавателя; известная
       комната резервируется, остальные пары учитывают заданный буфер на дорогу.
       Снимок ПД возвращается в fixed_pd_snapshot отдельно от scheduled_classes.
    4. CP-SAT размещает требуемые пары по слотам шаблона недели, выбирает
       аудитории (вместимость >= число слушателей). Сначала максимизируется
       число пар, затем приоритет лекций; дальше уменьшается ожидание студентов
       и преподавателей с учётом ПД и перегрузка преподавателей.
    5. Результат сохраняется как ScheduledClass + GenerationRun (+ Issues).
    """

    permission_classes: list = []

    @extend_schema(
        summary="Запустить генерацию расписания",
        description="Синхронный запуск решателя CP-SAT для семестра (опционально — "
                    "модуля). Возвращает созданный GenerationRun с результатом.",
        tags=["Генерация"],
        request=GenerateRequestSerializer,
        responses={status.HTTP_201_CREATED: GenerationRunSerializer},
    )
    @action(detail=False, methods=["post"], url_path="generate")
    def generate(self, request):
        serializer = GenerateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            outcome = run_generation(**serializer.validated_data)
        except GenerationError as exc:
            return Response({"detail": str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)
        run = GenerationRun.objects.prefetch_related(
            Prefetch("scheduled_classes", queryset=with_schedule_details(ScheduledClass.objects.all())),
            "issues").get(pk=outcome.run_id)
        return Response(GenerationRunSerializer(run).data,
                        status=status.HTTP_201_CREATED)
