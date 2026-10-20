from django.urls import path

from . import views

urlpatterns = [
    path("pair-times/", views.pair_times),
    path("groups/", views.groups),
    path("groups/<path:title>/lessons/", views.group_lessons),
    path("teachers/", views.teachers),
    path("teachers/<int:teacher_id>/lessons/", views.teacher_lessons),
    path("rooms/free/", views.free_rooms),
]
