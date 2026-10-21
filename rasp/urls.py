from django.urls import path

from . import views

urlpatterns = [
    path("pair-times/", views.pair_times),
    path("groups/", views.groups),
    path("groups/<path:title>/lessons/", views.group_lessons),
    path("teachers/", views.teachers),
    path("teachers/<int:teacher_id>/lessons/", views.teacher_lessons),
    path("rooms/free/", views.free_rooms),
    path("rooms/status/", views.rooms_status),
    path("pd/directions/", views.pd_directions),
    path("pd/projects/", views.pd_projects),
    path("pd/enrollments/", views.pd_enrollments),
    path("pd/enrollments/<int:enrollment_id>/", views.pd_enrollment_delete),
]
