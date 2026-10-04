"""URL-конфиг генератора расписания."""

from django.urls import path

from .views import GenerateView, ScheduleRunDetailView

urlpatterns = [
    path("generate/", GenerateView.as_view(), name="schedule-generate"),
    path("runs/<int:pk>/", ScheduleRunDetailView.as_view(), name="schedule-run-detail"),
]
