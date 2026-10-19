"""URL-конфиг генератора расписания.

Карта фичи: URL -> ViewSet (api/views.py) -> сервис (services/).

Группы маршрутов (все — под /api/schedule/):
- CRUD справочников: semesters, week-patterns, time-slots, modules, groups,
  students, teachers, subjects, locations, buildings, rooms, streams,
  curricula, curriculum-entries, projects, project-enrollments, events;
- результаты генерации (read-only): runs (+classes/issues), scheduled-classes,
  issues;
- просмотр расписания: schedule/groups|streams|teachers|students;
- schedule/common-free-slots, schedule/free-rooms — общие окна студентов и
  свободные аудитории; runs/<id>/wellbeing — усталость и окна запуска;
- POST /api/schedule/replan/ — минимальная перестройка после сбоя;
- POST /api/schedule/generate/ — запуск решателя (action viewset'а
  GenerateViewSet; url_path="generate" даёт ровно "/generate/", без повтора
  сегмента — совместимость с прежним APIView-маршрутом).
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api.views import (
    BuildingViewSet,
    CommonFreeSlotsViewSet,
    CurriculumEntryViewSet,
    CurriculumViewSet,
    FreeRoomsViewSet,
    GenerateViewSet,
    GenerationIssueViewSet,
    GenerationRunViewSet,
    GroupScheduleViewSet,
    GroupViewSet,
    LectureStreamViewSet,
    LessonEventViewSet,
    LocationViewSet,
    ModuleViewSet,
    ProjectEnrollmentViewSet,
    ProjectViewSet,
    ReplanViewSet,
    RoomViewSet,
    ScheduledClassViewSet,
    SemesterViewSet,
    StreamScheduleViewSet,
    StudentScheduleViewSet,
    StudentViewSet,
    SubjectViewSet,
    TeacherScheduleViewSet,
    TeacherViewSet,
    TimeSlotViewSet,
    WeekPatternViewSet,
)

router = DefaultRouter()

# --- Справочники (CRUD) -----------------------------------------------------
router.register("semesters", SemesterViewSet, basename="semester")
router.register("week-patterns", WeekPatternViewSet, basename="week-pattern")
router.register("time-slots", TimeSlotViewSet, basename="time-slot")
router.register("modules", ModuleViewSet, basename="module")
router.register("groups", GroupViewSet, basename="group")
router.register("students", StudentViewSet, basename="student")
router.register("teachers", TeacherViewSet, basename="teacher")
router.register("subjects", SubjectViewSet, basename="subject")
router.register("locations", LocationViewSet, basename="location")
router.register("buildings", BuildingViewSet, basename="building")
router.register("rooms", RoomViewSet, basename="room")
router.register("streams", LectureStreamViewSet, basename="stream")
router.register("curricula", CurriculumViewSet, basename="curriculum")
router.register("curriculum-entries", CurriculumEntryViewSet,
                basename="curriculum-entry")
router.register("projects", ProjectViewSet, basename="project")
router.register("project-enrollments", ProjectEnrollmentViewSet,
                basename="project-enrollment")
router.register("events", LessonEventViewSet, basename="event")

# --- Результаты генерации (read-only) ---------------------------------------
router.register("runs", GenerationRunViewSet, basename="schedule-run")
router.register("scheduled-classes", ScheduledClassViewSet,
                basename="scheduled-class")
router.register("issues", GenerationIssueViewSet, basename="generation-issue")

# --- Просмотр расписания потребителями --------------------------------------
router.register("schedule/groups", GroupScheduleViewSet, basename="schedule-group")
router.register("schedule/streams", StreamScheduleViewSet, basename="schedule-stream")
router.register("schedule/teachers", TeacherScheduleViewSet, basename="schedule-teacher")
router.register("schedule/students", StudentScheduleViewSet, basename="schedule-student")
router.register("schedule/common-free-slots", CommonFreeSlotsViewSet,
                basename="schedule-common-free-slots")
router.register("schedule/free-rooms", FreeRoomsViewSet, basename="schedule-free-rooms")

# --- Перепланирование после сбоя ----------------------------------------------
router.register("replan", ReplanViewSet, basename="schedule-replan")

# --- Запуск генерации ---------------------------------------------------------
# url_path="generate" у action сам по себе даёт "/{prefix}/generate/",
# поэтому префикс здесь — "" (корень include'а), чтобы итоговый URL остался
# ровно /api/schedule/generate/ (совместимость с прежним APIView-маршрутом).
router.register("", GenerateViewSet, basename="schedule-generate")

urlpatterns = [
    path("", include(router.urls)),
]
