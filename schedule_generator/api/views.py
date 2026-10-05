"""HTTP-представление фичи «генерация расписания».

Здесь ТОЛЬКО протокол: разбор/валидация запроса -> вызов сервисного слоя
(services/generation.run_generation) -> сериализация ответа. Никакой
бизнес-логики и работы с решателем — всё это видно в services/.

Эндпоинты (см. urls.py):
- POST /api/schedule/generate/   — запустить генерацию, вернуть GenerationRun;
- GET  /api/schedule/runs/<id>/  — посмотреть результат запуска.
"""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import GenerationRun
from ..services import GenerationError, run_generation
from .serializers import GenerateRequestSerializer, GenerationRunSerializer


class GenerateView(APIView):
    """POST /api/schedule/generate/ — запуск генерации расписания (OR-Tools CP-SAT).

    Что происходит внутри (детали — в services/generation.py):
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
        try:
            outcome = run_generation(**serializer.validated_data)
        except GenerationError as exc:
            return Response({"detail": str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)
        run = GenerationRun.objects.get(pk=outcome.run_id)
        return Response(GenerationRunSerializer(run).data,
                        status=status.HTTP_201_CREATED)


class ScheduleRunDetailView(APIView):
    """GET /api/schedule/runs/<id>/ — результат запуска генерации."""

    permission_classes: list = []

    def get(self, request, pk: int):
        run = GenerationRun.objects.filter(pk=pk).first()
        if run is None:
            return Response({"detail": "Запуск не найден."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(GenerationRunSerializer(run).data)
