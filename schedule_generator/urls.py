"""URL-конфиг генератора расписания.

Карта фичи: URL -> ViewSet (api/views.py) -> сервис (services/).

Группы маршрутов (все — под /api/schedule/):
- CRUD справочников: semesters, week-patterns, time-slots, modules, groups,
  students, teachers, subjects, locations, buildings, rooms, streams,
  curricula, curriculum-entries, projects, project-enrollments, events;
- результаты генерации (read-only): runs (+classes/issues), scheduled-classes,
  issues;
- просмотр расписания: schedule/groups|streams|teachers|students;
- POST /api/schedule/generate/ — запуск решателя (action viewset'а
  GenerateViewSet; url_path="generate" даёт ровно "/generate/", без повтора
  сегмента — совместимость с прежним APIView-маршрутом);
- API для фронтенда (агрегированные ответы под экраны, тег Frontend):
  frontend/bootstrap/, frontend/weeks/, frontend/grid/holders/,
  frontend/grid/, frontend/grid/calendar/, frontend/day/,
  frontend/me/students/<id>/, frontend/me/teachers/<id>/.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api.frontend import (
    BootstrapViewSet,
    CalendarGridViewSet,
    DayScheduleViewSet,
    GridHolderViewSet,
    PeriodWeekViewSet,
    StudentMeViewSet,
    TeacherMeViewSet,
    WeekGridViewSet,
)
from .api.views import (
    BuildingViewSet,
    CurriculumEntryViewSet,
    CurriculumViewSet,
    GenerationIssueViewSet,
    GenerationRunViewSet,
    GenerateViewSet,
    GroupScheduleViewSet,
    GroupViewSet,
    LectureStreamViewSet,
    LessonEventViewSet,
    LocationViewSet,
    ModuleViewSet,
    ProjectEnrollmentViewSet,
    ProjectViewSet,
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

# --- Запуск генерации ---------------------------------------------------------
# url_path="generate" у action сам по себе даёт "/{prefix}/generate/",
# поэтому префикс здесь — "" (корень include'а), чтобы итоговый URL остался
# ровно /api/schedule/generate/ (совместимость с прежним APIView-маршрутом).
router.register("", GenerateViewSet, basename="schedule-generate")

# --- API для фронтенда (агрегированные ответы под экраны) --------------------
frontend_router = DefaultRouter()
frontend_router.register("bootstrap", BootstrapViewSet, basename="fe-bootstrap")
frontend_router.register("weeks", PeriodWeekViewSet, basename="fe-weeks")
frontend_router.register("grid/holders", GridHolderViewSet,
                         basename="fe-grid-holders")
frontend_router.register("grid/calendar", CalendarGridViewSet,
                         basename="fe-grid-calendar")
frontend_router.register("grid", WeekGridViewSet, basename="fe-grid")
frontend_router.register("day", DayScheduleViewSet, basename="fe-day")
frontend_router.register("me/students", StudentMeViewSet,
                         basename="fe-me-student")
frontend_router.register("me/teachers", TeacherMeViewSet,
                         basename="fe-me-teacher")

urlpatterns = [
    path("", include(router.urls)),
    path("frontend/", include(frontend_router.urls)),
]
