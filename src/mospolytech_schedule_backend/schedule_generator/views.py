from django.db import transaction
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .generator import build_event_specs, generate_schedule
from .models import (
    GenerationIssue,
    GenerationRun,
    LessonEvent,
    Room,
    ScheduledClass,
    Semester,
    Teacher,
    TimeSlot,
    WeekPattern,
)


class GenerateRequestSerializer(serializers.Serializer):
    """Параметры запуска генерации недельного шаблона расписания."""

    semester = serializers.PrimaryKeyRelatedField(queryset=Semester.objects.all())
    week_pattern = serializers.PrimaryKeyRelatedField(
        queryset=WeekPattern.objects.all(), required=False, allow_null=True,
        help_text="Шаблон недели; по умолчанию — первый паттерн семестра.",
    )
    time_limit_seconds = serializers.IntegerField(required=False, default=30, min_value=1,
                                                  max_value=600)
    seed = serializers.IntegerField(required=False, default=17)
    clear_previous = serializers.BooleanField(
        required=False, default=True,
        help_text="Удалить пары предыдущих запусков этого семестра перед записью новых.",
    )

    def validate(self, attrs):
        wp = attrs.get("week_pattern")
        semester = attrs["semester"]
        if wp is not None and wp.semester_id != semester.id:
            raise serializers.ValidationError(
                {"week_pattern": "Шаблон недели относится к другому семестру."}
            )
        return attrs


class ScheduledClassSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduledClass
        fields = ["id", "event", "slot", "room"]


class GenerationRunSerializer(serializers.ModelSerializer):
    scheduled_classes = ScheduledClassSerializer(many=True, read_only=True)
    issues = serializers.StringRelatedField(many=True, read_only=True)

    class Meta:
        model = GenerationRun
        fields = ["id", "semester", "created_at", "status", "objective_value",
                  "solve_time_seconds", "message", "scheduled_classes", "issues"]


class GenerateView(APIView):
    """POST /api/schedule/generate/ — запуск генерации расписания (OR-Tools CP-SAT).

    Алгоритм:
    1. Берутся все LessonEvent семестра; физкультура (PE) исключается —
       она не генерируется (студент приходит в любое время/место).
    2. Лекции планируются онлайн на потоки групп (один слот на поток),
       практики/лаборатории — очно на группу, ПД — очно на студентов,
       выбравших проект, внеучебка — очно или онлайн (поле format).
    3. CP-SAT размещает требуемые пары по слотам шаблона недели, выбирает
       аудитории (вместимость >= размер аудитории), минимизирует окна и
       перегрузку преподавателей.
    4. Результат сохраняется как ScheduledClass + GenerationRun (+ Issues).
    """

    permission_classes: list = []

    def post(self, request):
        serializer = GenerateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = self.run_generation(**serializer.validated_data)
        run = GenerationRun.objects.get(pk=result["run_id"])
        return Response(GenerationRunSerializer(run).data,
                        status=status.HTTP_201_CREATED)

    @staticmethod
    def run_generation(*, semester, week_pattern=None, time_limit_seconds=30,
                       seed=17, clear_previous=True) -> dict:
        run = GenerationRun.objects.create(semester=semester, status="PENDING")
        try:
            if week_pattern is None:
                week_pattern = semester.week_patterns.order_by("id").first()
            if week_pattern is None:
                run.status = "ERROR"
                run.message = "У семестра нет ни одного шаблона недели (TimeSlot)."
                run.save()
                return {"run_id": run.id}

            slots = list(TimeSlot.objects.filter(week_pattern=week_pattern)
                         .order_by("day_of_week", "lesson_number"))
            rooms = list(Room.objects.all())
            events = LessonEvent.objects.filter(semester=semester)
            specs, skipped = build_event_specs(events)

            teachers_by_id = {
                t.id: {"max_per_day": t.max_pairs_per_day}
                for t in Teacher.objects.all()
            }

            gen = generate_schedule(
                slots=slots,
                rooms=rooms,
                specs=specs,
                teachers_by_id=teachers_by_id,
                time_limit_seconds=time_limit_seconds,
                seed=seed,
            )

            slot_by_id = {s.id: s for s in slots}
            room_ids = {r.id for r in rooms}

            with transaction.atomic():
                if clear_previous:
                    ScheduledClass.objects.filter(run__semester=semester).delete()
                objs = [
                    ScheduledClass(
                        event_id=a["event_id"],
                        slot=slot_by_id[a["slot_id"]],
                        room_id=a["room_id"] if a["room_id"] in room_ids else None,
                        run=run,
                    )
                    for a in gen.assignments
                ]
                ScheduledClass.objects.bulk_create(objs)

                for ev, reason in skipped:
                    GenerationIssue.objects.create(
                        run=run, event=None, level="WARNING",
                        text=f"Событие пропущено ({reason}): #{ev.pk} {ev}",
                    )
                unscheduled_ids = set(gen.unscheduled_event_ids)
                if unscheduled_ids:
                    ev_by_id = {e.id: e for e in events.filter(id__in=unscheduled_ids)}
                    for eid in sorted(unscheduled_ids):
                        GenerationIssue.objects.create(
                            run=run, event=ev_by_id.get(eid), level="WARNING",
                            text=f"Не все обязательные пары события запланированы: #{eid}",
                        )

                run.status = gen.status
                run.objective_value = gen.objective_value
                run.solve_time_seconds = gen.solve_time
                run.message = gen.message
                run.save()
        except Exception as exc:  # noqa: BLE001
            run.status = "ERROR"
            run.message = str(exc)
            run.save()
            raise
        return {"run_id": run.id}


class ScheduleRunDetailView(APIView):
    """GET /api/schedule/runs/<id>/ — результат запуска генерации."""

    permission_classes: list = []

    def get(self, request, pk: int):
        run = GenerationRun.objects.filter(pk=pk).first()
        if run is None:
            return Response({"detail": "Запуск не найден."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(GenerationRunSerializer(run).data)
