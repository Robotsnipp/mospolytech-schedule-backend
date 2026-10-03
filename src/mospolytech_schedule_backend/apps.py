from django.apps import AppConfig


class ScheduleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mospolytech_schedule_backend"
    verbose_name = "Генерация расписания пар"
