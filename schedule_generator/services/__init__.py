"""Сервисный слой генератора расписания.

Слои приложения (зависимости направлены строго «вниз»):

    models/  ->  services/  ->  api/ (serializers + views)

- ``models/``   — только данные и инварианты предметной области;
- ``services/`` — бизнес-логика: подготовка данных, решение задачи CP-SAT,
  запись результата запуска; ничего не знает про HTTP;
- ``api/``      — представление: эндпоинты и сериализаторы DRF, тонкая обёртка
  над сервисами.

Точка входа для внешнего кода — ``run_generation`` (см. generation.py).

Модули:
- ``generation``     — оркестрация полного цикла генерации (единственная точка
                       входа: view, CLI, Celery);
- ``generator``      — модель OR-Tools CP-SAT (сборка и решение);
- ``event_specs``    — перевод LessonEvent (ORM) в EventSpec (вход модели);
- ``constraints``    — жёсткие ограничения модели (один носитель на слот и т.п.);
- ``objectives``     — мягкие ограничения и целевая функция (окна, перегрузка);
- ``solution``       — извлечение присваиваний из решения решателя;
- ``config``         — параметры решателя и веса целевой функции;
- ``result_writer``  — сохранение результата в GenerationRun/ScheduledClass/Issues;
- ``dto``            — типы, передаваемые между модулями (EventSpec, результаты).
"""

from .config import SolverConfig
from .dto import EventSpec, GenerationResult, RunOutcome
from .generation import GenerationError, run_generation

__all__ = [
    "EventSpec",
    "GenerationError",
    "GenerationResult",
    "RunOutcome",
    "SolverConfig",
    "run_generation",
]
