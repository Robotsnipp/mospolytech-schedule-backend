from django.contrib import admin

from .models import (
    GenerationIssue,
    GenerationRun,
    Group,
    LectureStream,
    LessonEvent,
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


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "semester", "is_first_year_first_semester")
    search_fields = ("name",)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("full_name", "group")
    search_fields = ("full_name",)
    list_filter = ("group__semester",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("title", "semester", "supervisor", "max_students")
    search_fields = ("title",)


@admin.register(ProjectEnrollment)
class ProjectEnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "project")
    autocomplete_fields = ("student", "project")


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    search_fields = ("full_name",)


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "capacity", "allows_lecture")


@admin.register(LectureStream)
class LectureStreamAdmin(admin.ModelAdmin):
    list_display = ("subject", "semester", "teacher")
    filter_horizontal = ("groups",)


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


@admin.register(GenerationRun)
class GenerationRunAdmin(admin.ModelAdmin):
    list_display = ("id", "semester", "created_at", "status",
                    "objective_value", "solve_time_seconds")
    readonly_fields = ("created_at",)


@admin.register(GenerationIssue)
class GenerationIssueAdmin(admin.ModelAdmin):
    list_display = ("run", "level", "event", "text")
    list_filter = ("level",)
