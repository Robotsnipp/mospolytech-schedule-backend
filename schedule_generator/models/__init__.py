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
    dpo.py        Программы ДПО: DPOProgram, DPOMembership, DPODiscipline.

Правила для этого слоя:
- только данные и инварианты (clean/__str__/свойства);
- никаких импортов из services/ и api/;
- бизнес-логика генерации живёт в services/, HTTP — в api/.
"""

from .calendar import Module, Semester, TimeSlot, WeekPattern
from .curriculum import Curriculum, CurriculumEntry, StudyForm
from .dpo import DPODiscipline, DPOMembership, DPOProgram
from .fixed_pd import FixedProjectSession
from .generation import GenerationIssue, GenerationRun
from .imported_schedule import ImportedGroupSchedule, ImportedLesson, ScheduleImport
from .lessons import LessonEvent, ScheduledClass
from .manual import ManualPlacement, ManualScheduleVariant
from .pe import PEDirection, PEEnrollment, PESlot
from .people import Group, Student, Teacher
from .projects import Project, ProjectEnrollment
from .resources import Building, LectureStream, Location, ResourceProfile, Room, Subject
from .semester_generation import SemesterGeneration, SemesterGenerationModule

__all__ = [
    "ImportedGroupSchedule", "ImportedLesson", "ScheduleImport",
    "FixedProjectSession",
    "DPODiscipline", "DPOMembership", "DPOProgram",
    "Module", "Curriculum", "CurriculumEntry", "StudyForm", "Location", "Building",
    "ManualPlacement", "ManualScheduleVariant",
    "PEDirection", "PEEnrollment", "PESlot",
    "GenerationIssue",
    "GenerationRun",
    "Group",
    "LectureStream",
    "LessonEvent",
    "Project",
    "ProjectEnrollment",
    "Room",
    "ResourceProfile",
    "ScheduledClass",
    "Semester",
    "SemesterGeneration", "SemesterGenerationModule",
    "Student",
    "Subject",
    "Teacher",
    "TimeSlot",
    "WeekPattern",
]
