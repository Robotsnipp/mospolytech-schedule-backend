"""Пакет моделей предметной области генератора расписания.

Как устроена фича (что нужно запланировать и что получается на выходе):

    calendar.py   Календарь: Semester -> WeekPattern -> TimeSlot
                  (недели, дни, пары в день — «сетка» расписания).
    people.py     Участники: Group, Student, Teacher.
    projects.py   Проектная деятельность (ПД): Project, ProjectEnrollment —
                  студент сам выбирает проект; «группа» по ПД = все выбравшие
                  один проект (не зависит от учебной группы).
    resources.py  Ресурсы занятий: Subject, Room, LectureStream
                  (поток лекций — несколько групп смотрят одну онлайн-лекцию).
    lessons.py    Планирование: LessonEvent («что нужно запланировать») и
                  ScheduledClass («результат генерации: пара в слот/аудиторию»).
    generation.py Журнал запусков: GenerationRun, GenerationIssue.

Правила для этого слоя:
- только данные и инварианты (clean/__str__/свойства);
- никаких импортов из services/ и api/;
- бизнес-логика генерации живёт в services/, HTTP — в api/.
"""

from .calendar import Semester, TimeSlot, WeekPattern
from .generation import GenerationIssue, GenerationRun
from .lessons import LessonEvent, ScheduledClass
from .people import Group, Student, Teacher
from .projects import Project, ProjectEnrollment
from .resources import LectureStream, Room, Subject

__all__ = [
    "GenerationIssue",
    "GenerationRun",
    "Group",
    "LectureStream",
    "LessonEvent",
    "Project",
    "ProjectEnrollment",
    "Room",
    "ScheduledClass",
    "Semester",
    "Student",
    "Subject",
    "Teacher",
    "TimeSlot",
    "WeekPattern",
]
