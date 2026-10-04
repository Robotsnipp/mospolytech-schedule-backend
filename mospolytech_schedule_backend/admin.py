from django.contrib import admin

from . import models


@admin.register(models.Semester)
class SemesterAdmin(admin.ModelAdmin):
    list_display = ("name", "year", "number", "start_date", "end_date")


@admin.register(models.Subject)
class SubjectAdmin(admin.ModelAdmin):
    search_fields = ("name", "code")


@admin.register(models.ClassType)
class ClassTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "format", "multi_group", "generate", "project_based")
    list_filter = ("format", "multi_group", "generate", "project_based")


@admin.register(models.Department)
class DepartmentAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(models.Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "department")
    search_fields = ("last_name", "first_name")


@admin.register(models.Building)
class BuildingAdmin(admin.ModelAdmin):
    search_fields = ("name", "address")


@admin.register(models.Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("building", "number", "capacity")
    list_filter = ("building",)


@admin.register(models.Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "course", "department")
    list_filter = ("course", "department")


@admin.register(models.Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "group")
    search_fields = ("last_name", "first_name")
    list_filter = ("group",)


@admin.register(models.Project)
class ProjectAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(models.ProjectChoice)
class ProjectChoiceAdmin(admin.ModelAdmin):
    list_display = ("student", "project", "semester")


@admin.register(models.ProjectGroup)
class ProjectGroupAdmin(admin.ModelAdmin):
    list_display = ("project", "semester")
    filter_horizontal = ("members",)


@admin.register(models.WeekPattern)
class WeekPatternAdmin(admin.ModelAdmin):
    pass


@admin.register(models.TimeSlot)
class TimeSlotAdmin(admin.ModelAdmin):
    list_display = ("number", "start_time", "end_time")


@admin.register(models.EventSlot)
class EventSlotAdmin(admin.ModelAdmin):
    list_display = ("subject", "class_type", "teacher", "weekday", "time_slot", "room", "is_generated")
    list_filter = ("class_type", "semester", "is_generated")
    filter_horizontal = ("groups",)
