"""URLs dev-панели. Подключаются в config/urls.py только при DEBUG=True."""

from django.urls import path

from . import views

app_name = "dev_dashboard"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("generate/", views.generate, name="generate"),
]
