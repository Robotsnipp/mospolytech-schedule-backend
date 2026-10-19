"""CRUD-сериализаторы справочников предметной области (слой представления).

Ответственность: только валидация входных данных HTTP и форматирование
выходных. Бизнес-логика генерации — в services/, инварианты моделей — в models/.

Общие соглашения:
- ``display`` — человекочитаемое представление объекта (из Model.__str__),
  удобно клиентам без сборки подписи из FK;
- поля с choices дополнительно отдаются как ``*_display`` (только для чтения);
- связанные объекты выводятся PK-полем (``group``) и именем (``group_name``),
  чтобы список был читаемым за один запрос (без N+1);
- валидация бизнес-правил вызывается через ``instance.full_clean(exclude=...)``
  — те же инварианты, что гарантирует админка и слой моделей. Исключения по
  ``exclude`` снимаются только там, где поле задаётся на уровне связанного
  объекта (например, локация аудитории выводится из корпуса).
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from ..enums import LessonType
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

# --------------------------------------------------------------------------
# Календарь: семестр -> шаблон недели -> слоты; модули семестра
# --------------------------------------------------------------------------

class SemesterSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)

    class Meta:
        model = Semester
        fields = ["id", "name", "start_date", "weeks_count", "display"]
        extra_kwargs = {"name": {"help_text": "Название семестра, например «2026/27 осень»."}}


class WeekPatternSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)
    semester_name = serializers.CharField(source="semester.name", read_only=True)
    parity_display = serializers.SerializerMethodField()

    class Meta:
        model = WeekPattern
        fields = ["id", "semester", "semester_name", "name", "parity",
                  "parity_display", "display"]
        read_only_fields = ["semester_name", "parity_display"]

    @extend_schema_field(serializers.CharField())
    def get_parity_display(self, obj):
        return dict(WeekPattern._meta.get_field("parity").choices).get(obj.parity, "")

    def validate(self, attrs):
        instance = self.instance
        merged_semester = attrs.get("semester", getattr(instance, "semester", None))
        merged_name = attrs.get("name", getattr(instance, "name", None))
        conflict = WeekPattern.objects.filter(semester=merged_semester,
                                             name=merged_name)
        if instance is not None:
            conflict = conflict.exclude(pk=instance.pk)
        if conflict.exists():
            raise serializers.ValidationError(
                {"name": "В этом семестре уже есть шаблон с таким названием."})
        return attrs


class TimeSlotSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)
    week_pattern_name = serializers.CharField(source="week_pattern.name",
                                              read_only=True)
    day_of_week_display = serializers.SerializerMethodField()

    class Meta:
        model = TimeSlot
        fields = ["id", "week_pattern", "week_pattern_name", "day_of_week",
                  "day_of_week_display", "lesson_number", "start_time",
                  "end_time", "display"]
        read_only_fields = ["week_pattern_name", "day_of_week_display"]

    @extend_schema_field(serializers.CharField())
    def get_day_of_week_display(self, obj):
        return dict(TimeSlot.DAY_CHOICES).get(obj.day_of_week, "")

    def validate(self, attrs):
        instance = self.instance
        pattern = attrs.get("week_pattern", getattr(instance, "week_pattern", None))
        day = attrs.get("day_of_week", getattr(instance, "day_of_week", None))
        number = attrs.get("lesson_number", getattr(instance, "lesson_number", None))
        if pattern is not None:
            conflict = TimeSlot.objects.filter(week_pattern=pattern,
                                               day_of_week=day,
                                               lesson_number=number)
            if instance is not None:
                conflict = conflict.exclude(pk=instance.pk)
            if conflict.exists():
                raise serializers.ValidationError(
                    {"lesson_number": "В этот день шаблона уже есть пара с таким номером."})
        start = attrs.get("start_time", getattr(instance, "start_time", None))
        end = attrs.get("end_time", getattr(instance, "end_time", None))
        if start and end and end <= start:
            raise serializers.ValidationError({"end_time": "Конец должен быть позже начала."})
        return attrs


class ModuleSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)
    semester_name = serializers.CharField(source="semester.name", read_only=True)

    class Meta:
        model = Module
        fields = ["id", "semester", "semester_name", "number", "start_date",
                  "end_date", "teaching_weeks", "display"]
        read_only_fields = ["semester_name"]

    def validate(self, attrs):
        instance = self.instance
        merged = {
            "semester": attrs.get("semester", getattr(instance, "semester", None)),
            "number": attrs.get("number", getattr(instance, "number", None)),
            "start_date": attrs.get("start_date", getattr(instance, "start_date", None)),
            "end_date": attrs.get("end_date", getattr(instance, "end_date", None)),
            "teaching_weeks": attrs.get("teaching_weeks",
                                        getattr(instance, "teaching_weeks", None)),
        }
        if None in (merged["semester"], merged["start_date"], merged["end_date"]):
            return attrs
        if merged["end_date"] < merged["start_date"]:
            raise serializers.ValidationError({"end_date": "Конец раньше начала модуля."})
        if merged["semester"].start_date and merged["start_date"] < merged["semester"].start_date:
            raise serializers.ValidationError(
                {"start_date": "Модуль начинается раньше семестра."})
        conflict = Module.objects.filter(semester=merged["semester"],
                                         number=merged["number"])
        if instance is not None:
            conflict = conflict.exclude(pk=instance.pk)
        if conflict.exists():
            raise serializers.ValidationError(
                {"number": "В этом семестре уже есть модуль с таким номером."})
        return attrs


# --------------------------------------------------------------------------
# Участники: группы, студенты, преподаватели
# --------------------------------------------------------------------------

class GroupSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)
    students_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Group
        fields = ["id", "name", "admission_year", "semester", "curriculum",
                  "is_first_year_first_semester", "is_dpo", "display",
                  "students_count"]
        extra_kwargs = {
            "name": {"help_text": "Учебная группа: «241-334». Группа ДПО: «ДПО-<программа>»."},
            "admission_year": {"required": False},
        }

    def create(self, validated_data):
        group = Group(**validated_data)
        group.full_clean(exclude=["admission_year"])
        group.save()
        return group

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.full_clean(exclude=["admission_year"])
        instance.save()
        return instance


class StudentSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)
    group_name = serializers.CharField(source="group.name", read_only=True)

    class Meta:
        model = Student
        fields = ["id", "group", "group_name", "full_name", "display"]
        read_only_fields = ["group_name"]


class TeacherSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)

    class Meta:
        model = Teacher
        fields = ["id", "full_name", "max_pairs_per_day", "max_pairs_per_week",
                  "display"]


# --------------------------------------------------------------------------
# Ресурсы: дисциплины, локации, корпуса, аудитории, лекционные потоки
# --------------------------------------------------------------------------

class SubjectSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)

    class Meta:
        model = Subject
        fields = ["id", "name", "display"]


class LocationSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)

    class Meta:
        model = Location
        fields = ["id", "name", "address", "display"]


class BuildingSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = Building
        fields = ["id", "location", "location_name", "name", "display"]
        read_only_fields = ["location_name"]


class RoomSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)
    building_name = serializers.CharField(source="building.name", read_only=True,
                                          default=None)
    location_name = serializers.SerializerMethodField()

    class Meta:
        model = Room
        fields = ["id", "name", "building", "building_name", "location_name",
                  "is_active", "room_type", "equipment", "capacity",
                  "allows_lecture", "display"]
        read_only_fields = ["building_name"]

    @extend_schema_field(serializers.CharField())
    def get_location_name(self, obj):
        return obj.location_name_for_ui()

    def validate_name(self, value):
        conflict = Room.objects.filter(name=value)
        if self.instance is not None:
            conflict = conflict.exclude(pk=self.instance.pk)
        if conflict.exists():
            raise serializers.ValidationError(
                "Аудитория с таким названием уже существует.")
        return value


class LectureStreamSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    teacher_name = serializers.CharField(source="teacher.full_name",
                                         read_only=True, default=None)
    group_names = serializers.SerializerMethodField()

    class Meta:
        model = LectureStream
        fields = ["id", "semester", "subject", "subject_name", "teacher",
                  "teacher_name", "groups", "group_names", "display"]
        read_only_fields = ["subject_name", "teacher_name"]

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_group_names(self, obj):
        return [g.name for g in obj.groups.all()]


# --------------------------------------------------------------------------
# Учебные планы
# --------------------------------------------------------------------------

class CurriculumSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)
    study_form_display = serializers.CharField(source="get_study_form_display",
                                               read_only=True)

    class Meta:
        model = Curriculum
        fields = ["id", "name", "program_code", "profile", "admission_year",
                  "study_form", "study_form_display", "duration_months",
                  "version", "source_file", "display"]
        extra_kwargs = {"source_file": {"required": False}}

    def validate(self, attrs):
        instance = self.instance
        merged = {
            key: attrs.get(key, getattr(instance, key, None))
            for key in ("program_code", "profile", "admission_year",
                        "study_form", "version")
        }
        if all(v is not None for v in merged.values()):
            conflict = Curriculum.objects.filter(**merged)
            if instance is not None:
                conflict = conflict.exclude(pk=instance.pk)
            if conflict.exists():
                raise serializers.ValidationError(
                    {"version": "План с таким кодом, профилем, набором, "
                                "формой и версией уже существует."})
        return attrs


class CurriculumEntrySerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)
    subject_name = serializers.CharField(source="subject.name", read_only=True)

    class Meta:
        model = CurriculumEntry
        fields = ["id", "curriculum", "subject", "subject_name",
                  "discipline_code", "semester_number", "lecture_hours",
                  "practice_hours", "lab_hours", "independent_hours",
                  "has_exam", "has_credit", "has_graded_credit", "display"]
        read_only_fields = ["subject_name"]

    def validate(self, attrs):
        instance = self.instance
        curriculum = attrs.get("curriculum", getattr(instance, "curriculum", None))
        code = attrs.get("discipline_code", getattr(instance, "discipline_code", None))
        sem_no = attrs.get("semester_number", getattr(instance, "semester_number", None))
        if curriculum and code and sem_no:
            conflict = CurriculumEntry.objects.filter(curriculum=curriculum,
                                                      discipline_code=code,
                                                      semester_number=sem_no)
            if instance is not None:
                conflict = conflict.exclude(pk=instance.pk)
            if conflict.exists():
                raise serializers.ValidationError(
                    {"discipline_code": "В этом плане уже есть строка с таким "
                                        "кодом и семестром обучения."})
        return attrs


# --------------------------------------------------------------------------
# Проектная деятельность (ПД)
# --------------------------------------------------------------------------

class ProjectSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)
    supervisor_name = serializers.CharField(source="supervisor.full_name",
                                            read_only=True, default=None)
    enrolled_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Project
        fields = ["id", "semester", "title", "supervisor", "supervisor_name",
                  "max_students", "display", "enrolled_count"]
        read_only_fields = ["supervisor_name"]


class ProjectEnrollmentSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)
    student_full_name = serializers.CharField(source="student.full_name",
                                              read_only=True)
    project_title = serializers.CharField(source="project.title", read_only=True)

    class Meta:
        model = ProjectEnrollment
        fields = ["id", "student", "student_full_name", "project",
                  "project_title", "display"]
        read_only_fields = ["student_full_name", "project_title"]

    def create(self, validated_data):
        enrollment = ProjectEnrollment(**validated_data)
        enrollment.full_clean()
        enrollment.save()
        return enrollment

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.full_clean()
        instance.save()
        return instance


# --------------------------------------------------------------------------
# Занятия: план (LessonEvent) и результат (ScheduledClass)
# --------------------------------------------------------------------------

class LessonEventSerializer(serializers.ModelSerializer):
    """Занятие-потребность: что нужно запланировать для носителя.

    Инварианты (ровно один носитель, тип↔носитель, тип↔формат) проверяются
    модельным ``clean()``; здесь добавляется проверка уникальности пары
    (носитель, дисциплина, тип, формат) внутри семестра, чтобы не плодить
    дубликаты потребностей.
    """

    display = serializers.CharField(source="__str__", read_only=True)
    lesson_type_display = serializers.SerializerMethodField()
    format_display = serializers.SerializerMethodField()
    effective_format = serializers.ReadOnlyField()
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    group_name = serializers.CharField(source="group.name", read_only=True,
                                       default=None)
    stream_label = serializers.CharField(source="stream.__str__", read_only=True,
                                         default=None)
    project_title = serializers.CharField(source="project.title", read_only=True,
                                          default=None)
    teacher_name = serializers.CharField(source="teacher.full_name",
                                         read_only=True, default=None)

    class Meta:
        model = LessonEvent
        fields = ["id", "semester", "subject", "subject_name", "lesson_type",
                  "lesson_type_display", "format", "format_display",
                  "effective_format", "group", "group_name", "stream",
                  "stream_label", "project", "project_title", "teacher",
                  "teacher_name", "module", "curriculum_entry",
                  "pairs_per_week", "audience_size", "display"]
        read_only_fields = ["subject_name", "group_name", "stream_label",
                            "project_title", "teacher_name"]

    @extend_schema_field(serializers.CharField())
    def get_lesson_type_display(self, obj):
        return LessonType(obj.lesson_type).label

    @extend_schema_field(serializers.CharField())
    def get_format_display(self, obj):
        from ..enums import Format
        return Format(obj.format).label if obj.format else ""

    def validate(self, attrs):
        instance = self.instance
        merged = {}
        for key in ("semester", "subject", "lesson_type", "format", "group",
                    "stream", "project", "module"):
            if key in attrs:
                merged[key] = attrs[key]
            elif instance is not None:
                merged[key] = getattr(instance, key, None)
        holders = [merged.get(k) for k in ("group", "stream", "project")]
        if sum(1 for h in holders if h) != 1:
            raise serializers.ValidationError({
                "holder": "У занятия должен быть ровно один носитель: группа, "
                          "лекционный поток или проект (ПД)."})
        duplicate_qs = LessonEvent.objects.filter(
            semester=merged.get("semester"),
            subject=merged.get("subject"),
            lesson_type=merged.get("lesson_type"),
            format=merged.get("format"),
            group=merged.get("group"),
            stream=merged.get("stream"),
            project=merged.get("project"),
            module=merged.get("module"),
        )
        if instance is not None:
            duplicate_qs = duplicate_qs.exclude(pk=instance.pk)
        if duplicate_qs.exists():
            raise serializers.ValidationError(
                {"detail": "Такое занятие уже запланировано в этом семестре "
                           "(дубликат можно создать через pairs_per_week)."})
        return attrs

    def create(self, validated_data):
        event = LessonEvent(**validated_data)
        event.full_clean(exclude=["curriculum_entry"])
        event.save()
        return event

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.full_clean(exclude=["curriculum_entry"])
        instance.save()
        return instance


class ScheduledClassSerializer(serializers.ModelSerializer):
    """Пара в расписании — результат генерации. Только чтение: создаёт её
    решатель (services/result_writer), вручную её менять нельзя."""

    display = serializers.CharField(source="__str__", read_only=True)
    slot_day = serializers.IntegerField(source="slot.day_of_week", read_only=True)
    slot_lesson_number = serializers.IntegerField(source="slot.lesson_number",
                                                  read_only=True)
    slot_start_time = serializers.TimeField(source="slot.start_time",
                                            read_only=True)
    slot_end_time = serializers.TimeField(source="slot.end_time", read_only=True)
    week_pattern = serializers.IntegerField(source="slot.week_pattern_id",
                                            read_only=True)
    subject_name = serializers.CharField(source="event.subject.name", read_only=True)
    lesson_type = serializers.CharField(source="event.lesson_type", read_only=True)
    group_name = serializers.CharField(source="event.group.name", read_only=True,
                                       default=None)
    stream_label = serializers.CharField(source="event.stream.__str__",
                                         read_only=True, default=None)
    project_title = serializers.CharField(source="event.project.title",
                                          read_only=True, default=None)
    teacher_name = serializers.CharField(source="event.teacher.full_name",
                                         read_only=True, default=None)
    room_name = serializers.CharField(source="room.name", read_only=True,
                                      default=None)
    location_name = serializers.SerializerMethodField()

    class Meta:
        model = ScheduledClass
        fields = ["id", "run", "event", "subject_name", "lesson_type",
                  "group_name", "stream_label", "project_title", "teacher_name",
                  "slot", "slot_day", "slot_lesson_number", "slot_start_time",
                  "slot_end_time", "week_pattern", "room", "room_name",
                  "location_name", "display"]

    @extend_schema_field(serializers.CharField())
    def get_location_name(self, obj):
        return obj.room.location_name_for_ui() if obj.room_id else ""


# --------------------------------------------------------------------------
# Журнал генерации
# --------------------------------------------------------------------------

class GenerationIssueSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source="__str__", read_only=True)
    level_display = serializers.CharField(source="get_level_display",
                                          read_only=True)

    class Meta:
        model = GenerationIssue
        fields = ["id", "run", "event", "level", "level_display", "text",
                  "display"]


class GenerationRunListSerializer(serializers.ModelSerializer):
    """Компактный вид запуска для списка (без пар и проблем)."""

    display = serializers.CharField(source="__str__", read_only=True)
    status_display = serializers.CharField(source="get_status_display",
                                           read_only=True)
    classes_count = serializers.IntegerField(read_only=True)
    issues_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = GenerationRun
        fields = ["id", "semester", "module", "created_at", "status",
                  "status_display", "objective_value", "solve_time_seconds",
                  "message", "display", "classes_count", "issues_count"]


class GenerationRunSerializer(serializers.ModelSerializer):
    """Полный вид запуска: результат решения со всеми парами и проблемами."""

    scheduled_classes = ScheduledClassSerializer(many=True, read_only=True)
    issues = GenerationIssueSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source="get_status_display",
                                           read_only=True)

    class Meta:
        model = GenerationRun
        fields = ["id", "semester", "module", "created_at", "status",
                  "status_display", "objective_value", "solve_time_seconds",
                  "message", "scheduled_classes", "issues"]
