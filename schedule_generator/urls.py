"""URL-конфиг генератора расписания.

Карта фичи: URL -> представление (api/views.py) -> сервис (services/).
"""

from django.urls import path

from .api.views import GenerateView, ScheduleRunDetailView

urlpatterns = [
    path("generate/", GenerateView.as_view(), name="schedule-generate"),
    path("runs/<int:pk>/", ScheduleRunDetailView.as_view(), name="schedule-run-detail"),
]
