"""Админ-интерфейс. Сгруппирован по поддоменам — как и models/.

- Календарь: Semester, WeekPattern (+ inline слотов), TimeSlot;
- Люди: Group, Student, Teacher;
- Проекты (ПД): Project, ProjectEnrollment;
- Ресурсы: Subject, Room, LectureStream;
- Занятия: LessonEvent (план), ScheduledClass (результат);
- Генерация: GenerationRun, GenerationIssue.
"""

from django.contrib import admin

from .models import (
    Building,
    Curriculum,
    CurriculumEntry,
    FixedProjectSession,
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

# ---------------- Календарь ----------------

class SlotInline(admin.TabularInline):
    model = TimeSlot
    extra = 0


@admin.register(Semester)
class SemesterAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "weeks_count")


@admin.register(WeekPattern)
class WeekPatternAdmin(admin.ModelAdmin):
    inlines = [SlotInline]
    list_display = ("name", "semester", "parity")


@admin.register(TimeSlot)
class TimeSlotAdmin(admin.ModelAdmin):
    list_display = ("week_pattern", "day_of_week", "lesson_number", "start_time", "end_time")
    list_filter = ("week_pattern",)


# ---------------- Люди ----------------

@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "admission_year", "curriculum", "semester",
                    "is_first_year_first_semester", "is_dpo")
    list_filter = ("is_dpo", "admission_year")
    search_fields = ("name",)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("full_name", "group")
    search_fields = ("full_name",)
    list_filter = ("group__semester",)


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    search_fields = ("full_name",)


# ---------------- Проекты (ПД) ----------------

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("title", "semester", "supervisor", "max_students")
    search_fields = ("title",)


@admin.register(ProjectEnrollment)
class ProjectEnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "project")
    autocomplete_fields = ("student", "project")


@admin.register(FixedProjectSession)
class FixedProjectSessionAdmin(admin.ModelAdmin):
    list_display = ("project", "module", "date", "day_of_week", "start_time", "end_time", "teacher", "room")
    list_filter = ("project__semester", "module", "day_of_week")
    autocomplete_fields = ("project", "teacher", "room")


# ---------------- Ресурсы ----------------

@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    search_fields = ("name",)
    list_display = ("name", "location_name", "building", "capacity", "room_type", "is_active")
    list_filter = ("building__location", "building", "is_active")

    @admin.display(description="Локация", ordering="building__location__name")
    def location_name(self, obj: Room) -> str:
        """Локация через корпус (свойство Room.location)."""
        loc = obj.location
        return loc.name if loc is not None else "—"


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    search_fields = ("name", "address")


@admin.register(Building)
class BuildingAdmin(admin.ModelAdmin):
    list_display = ("name", "location")
    list_filter = ("location",)


@admin.register(LectureStream)
class LectureStreamAdmin(admin.ModelAdmin):
    list_display = ("subject", "semester", "teacher")
    filter_horizontal = ("groups",)


# ---------------- Занятия ----------------

@admin.register(LessonEvent)
class LessonEventAdmin(admin.ModelAdmin):
    list_display = ("subject", "lesson_type", "format", "semester",
                    "group", "stream", "project", "teacher", "pairs_per_week")
    list_filter = ("lesson_type", "format", "semester")
    search_fields = ("subject__name",)


@admin.register(ScheduledClass)
class ScheduledClassAdmin(admin.ModelAdmin):
    list_display = ("event", "slot", "room", "run")
    list_filter = ("run", "slot__day_of_week")


# ---------------- Журнал генерации ----------------

@admin.register(GenerationRun)
class GenerationRunAdmin(admin.ModelAdmin):
    list_display = ("id", "semester", "created_at", "status",
                    "objective_value", "solve_time_seconds")
    readonly_fields = ("created_at", "fixed_pd_snapshot")


@admin.register(GenerationIssue)
class GenerationIssueAdmin(admin.ModelAdmin):
    list_display = ("run", "level", "event", "text")
    list_filter = ("level",)




class CurriculumEntryInline(admin.TabularInline):
    model = CurriculumEntry
    extra = 0


@admin.register(Curriculum)
class CurriculumAdmin(admin.ModelAdmin):
    list_display = ("name", "admission_year", "study_form", "version")
    list_filter = ("study_form", "admission_year")
    inlines = [CurriculumEntryInline]


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("semester", "number", "start_date", "end_date", "teaching_weeks")


admin.site.register(CurriculumEntry)
