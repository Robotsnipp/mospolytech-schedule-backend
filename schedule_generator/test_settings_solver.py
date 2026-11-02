"""Real CP-SAT / concrete-checker agreement for the editor's finite rules."""
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from .models import (
    GenerationRun,
    Group,
    LessonEvent,
    ResourceProfile,
    Room,
    Semester,
    Student,
    Subject,
    Teacher,
    TimeSlot,
    WeekPattern,
)
from .services.config import SolverConfig
from .services.dto import EventSpec, FixedPDSpec
from .services.planning_rules import occupied_numbers, validate_rules
from .services.settings import normalize_settings
from .services.settings_solver import solve_settings


def scenario(rules, *, days=2, pairs=3, required=3):
    slots = [SimpleNamespace(id=day*pairs+n, day_of_week=day, lesson_number=n,
                            start_time=time(8+n*2), end_time=time(9+n*2))
             for day in range(days) for n in range(1, pairs+1)]
    rooms = [SimpleNamespace(id=1, capacity=30, building_id=1, building=SimpleNamespace(location_id=1)),
             SimpleNamespace(id=2, capacity=30, building_id=2, building=SimpleNamespace(location_id=1))]
    specs = [EventSpec(event_id=1, lesson_type='PRACTICE', format='ONSITE', pairs_per_week=required,
                       audience_size=2, teacher_id=1, blocking_group_ids={1}, attending_student_ids={1,2})]
    settings = normalize_settings({'schema_version':1, 'rules':rules, 'search':{'time_limit_seconds':3}})
    snapshot = {'schema_version':1, 'settings':settings, 'students':[{'id':1},{'id':2}],
                'slots':[{'id':s.id,'day_of_week':s.day_of_week,'lesson_number':s.lesson_number,
                          'start_minute':s.start_time.hour*60,'end_minute':s.end_time.hour*60} for s in slots],
                'events':[{'id':1,'format':'ONSITE','student_ids':[1,2],'audience_size':2}],
                'fixed_pd':[], 'rooms':[{'id':r.id,'building_id':r.building_id} for r in rooms]}
    return slots, rooms, specs, snapshot


class SettingsSolverTests(SimpleTestCase):
    def solve(self, rules, **kwargs):
        slots, rooms, specs, snapshot = scenario(rules, **kwargs)
        result = solve_settings(slots=slots, rooms=rooms, specs=specs, fixed_pd=[], snapshot=snapshot,
                                config=SolverConfig(time_limit_seconds=3, num_search_workers=1))
        report = validate_rules(snapshot, result.assignments)
        return result, report, snapshot

    def test_off_and_all_required_rules_agree_with_concrete_checker(self):
        cases = [
            {}, {'free_days':[{'day':'mon','mode':'required'}]},
            {'earliest_start':{'value':'12:00','mode':'required'}},
            {'latest_end':{'value':'13:00','mode':'required'}},
            {'max_pairs_per_day':{'value':2,'mode':'required'}},
            {'max_break_minutes':{'value':60,'mode':'required'}},
            {'max_consecutive_pairs':{'value':2,'mode':'required'}},
            {'min_pairs_per_day':{'value':2,'mode':'required'}},
            {'max_study_days':{'value':1,'mode':'required'}},
            {'max_building_changes':{'value':0,'mode':'required'}},
            {'preferred_building':{'value':2,'mode':'required'}},
        ]
        for rules in cases:
            with self.subTest(rules=rules):
                result, report, _ = self.solve(rules)
                self.assertEqual(result.status, 'SOLVED')
                self.assertFalse(report['blocking'])
                self.assertIsNone(result.objective_value)
                self.assertTrue(result.solver_diagnostics['proven_optimal'])

    def test_each_preferred_rule_keeps_complete_load_and_matches_checker(self):
        for key, value in [('earliest_start','13:00'),('latest_end','11:00'),('max_pairs_per_day',1),
                           ('max_break_minutes',0),('max_consecutive_pairs',1),('min_pairs_per_day',3),
                           ('max_study_days',1),('max_building_changes',0),('preferred_building',2)]:
            with self.subTest(key=key):
                result, report, _ = self.solve({key:{'value':value,'mode':'preferred'}})
                self.assertEqual(len(result.assignments),3)
                self.assertFalse(report['blocking'])
        result, report, _ = self.solve({'free_days':[{'day':'mon','mode':'preferred'},{'day':'tue','mode':'preferred'}]})
        self.assertEqual(result.status,'SOLVED')
        self.assertTrue(report['warnings'])

    def test_free_day_has_priority_over_building(self):
        slots, rooms, specs, snapshot = scenario({'free_days':[{'day':'sat','mode':'preferred'}],
                                                'preferred_building':{'value':2,'mode':'preferred'}}, required=1)
        slots = slots[:2]
        slots[0].day_of_week = 4
        slots[1].day_of_week = 5
        snapshot['slots'] = [{**snapshot['slots'][0],'day_of_week':4}, {**snapshot['slots'][1],'day_of_week':5}]
        fixed = [FixedPDSpec(session_id=day,project_id=day,day_of_week=day,
                 start_minute=0,end_minute=1439,teacher_id=900+day,student_ids=frozenset({900+day}),
                 participant_group_ids=frozenset({900+day}),room_id=room_id,place_raw='',
                 travel_buffer_minutes=0,occurrence_dates=frozenset({date(2026,10,19+day)}))
                 for day,room_id in [(4,2),(5,1)]]
        # Independent projects occupy Fri B2 and Sat B1. The only alternatives
        # are Friday in the other building and Saturday in the desired building.
        result = solve_settings(slots=slots, rooms=rooms, specs=specs, fixed_pd=fixed,snapshot=snapshot,
                                config=SolverConfig(time_limit_seconds=3,num_search_workers=1))
        self.assertEqual(result.assignments, [{'event_id':1,'slot_id':slots[0].id,'room_id':1}])
        names = [item['level'] for item in result.solver_diagnostics['levels']]
        self.assertLess(names.index('free_days'), names.index('preferred_building'))

    def test_common_budget_preserves_first_incumbent(self):
        slots, rooms, specs, snapshot = scenario({})
        with patch('schedule_generator.services.settings_solver.monotonic', side_effect=[0,0,4,4]):
            result = solve_settings(slots=slots, rooms=rooms,specs=specs,fixed_pd=[],snapshot=snapshot,
                                    config=SolverConfig(time_limit_seconds=3,num_search_workers=1))
        self.assertEqual(result.status,'SOLVED')
        self.assertEqual(len(result.assignments),3)
        self.assertFalse(result.solver_diagnostics['proven_optimal'])
        self.assertEqual(result.solve_time,4)
        self.assertEqual(result.solver_diagnostics['unfinished_levels'][0],'lectures')

    def test_required_gaps_are_individual_not_daily_sum(self):
        slots, rooms, specs, snapshot = scenario({'max_break_minutes':{'value':30,'mode':'required'}},days=1)
        for i, slot in enumerate(slots):
            start=540+i*90
            slot.start_time=time(start//60,start%60)
            slot.end_time=time((start+60)//60,(start+60)%60)
            snapshot['slots'][i].update(start_minute=start,end_minute=start+60)
        result=solve_settings(slots=slots,rooms=rooms,specs=specs,fixed_pd=[],snapshot=snapshot,
                              config=SolverConfig(time_limit_seconds=3,num_search_workers=1))
        self.assertEqual(result.status,'SOLVED')
        report=validate_rules(snapshot,result.assignments)
        self.assertEqual(report['metrics']['0']['days'][0]['break_minutes'],[30,30])
        self.assertFalse(report['blocking'])

    def test_solver_counts_only_own_project_for_each_person(self):
        slots,rooms,specs,snapshot=scenario({'max_pairs_per_day':{'value':3,'mode':'required'}},days=1,pairs=5,required=1)
        fixed=[]
        for sid,first,last in [(1,slots[0],slots[1]),(2,slots[2],slots[3])]:
            fixed.append(FixedPDSpec(session_id=sid,project_id=sid,day_of_week=0,
                start_minute=first.start_time.hour*60,end_minute=last.end_time.hour*60,
                teacher_id=900+sid,student_ids=frozenset({sid}),participant_group_ids=frozenset({1}),
                room_id=None,place_raw=f'Партнёр {sid}',travel_buffer_minutes=0,
                occurrence_dates=frozenset({date(2026,10,19)})))
        snapshot['fixed_pd']=[{'session_id':row.session_id,'day_of_week':0,
            'start_minute':row.start_minute,'end_minute':row.end_minute,'student_ids':list(row.student_ids),
            'room_id':None,'place_raw':row.place_raw} for row in fixed]
        result=solve_settings(slots=slots,rooms=rooms,specs=specs,fixed_pd=fixed,snapshot=snapshot,
                              config=SolverConfig(time_limit_seconds=3,num_search_workers=1))
        self.assertEqual(result.status,'SOLVED')
        self.assertEqual(result.assignments[0]['slot_id'],slots[4].id)
        report=validate_rules(snapshot,result.assignments)
        self.assertFalse(report['blocking'])
        self.assertEqual([len(profile['days'][0]['occupied_pairs']) for profile in report['metrics'].values()],[3,3])

    def test_pd_outside_grid_cannot_satisfy_required_pair_count(self):
        slots,rooms,specs,snapshot=scenario({'max_pairs_per_day':{'value':4,'mode':'required'}})
        snapshot['fixed_pd']=[{'session_id':1,'day_of_week':0,'start_minute':60,'end_minute':120,
                               'student_ids':[1],'room_id':None,'place_raw':'Партнёр'}]
        with self.assertRaisesMessage(ValueError,'вне сетки'):
            solve_settings(slots=slots,rooms=rooms,specs=specs,fixed_pd=[],snapshot=snapshot,
                           config=SolverConfig(time_limit_seconds=3,num_search_workers=1))

    def test_literal_pd_counts_two_pairs_only_for_own_students_and_each_gap(self):
        _, _, _, snapshot = scenario({}, days=1, required=1)
        snapshot['slots'] = [
            {'id':n,'day_of_week':0,'lesson_number':n,'start_minute':start,'end_minute':end}
            for n,start,end in [(1,540,630),(2,640,730),(3,740,830),(4,870,960),(5,970,1060)]]
        self.assertEqual(occupied_numbers(0,870,1060,snapshot['slots']),{4,5})
        self.assertEqual(occupied_numbers(0,630,640,snapshot['slots']),set())
        snapshot['fixed_pd'] = [{'day_of_week':0,'start_minute':870,'end_minute':1060,'student_ids':[1],
                                 'room_id':None,'place_raw':'Площадка'}]
        snapshot['settings'] = normalize_settings({'schema_version':1,'rules':{'max_pairs_per_day':{'value':2,'mode':'required'},
                                                 'max_break_minutes':{'value':30,'mode':'preferred'}}})
        report=validate_rules(snapshot,[{'event_id':1,'slot_id':1,'room_id':1}])
        self.assertEqual(report['blocking'][0]['student_ids'],[1])
        self.assertEqual(report['blocking'][0]['actual'],3)
        self.assertEqual(report['warnings'][0]['actual'],240)
        self.assertEqual(len(report['metrics']),2)

    def test_settings_reject_unknown_and_wrong_types(self):
        for value in [{'schema_version':2}, {'schema_version':1,'rules':{'oops':{}}},
                      {'schema_version':1,'rules':{'max_study_days':{'value':True,'mode':'required'}}},
                      {'schema_version':1,'rules':{'earliest_start':{'value':'25:00','mode':'preferred'}}}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_settings(value)


class SettingsContractTests(TestCase):
    def setUp(self):
        self.client=APIClient()
        self.client.force_authenticate(get_user_model().objects.create_user(username='settings-test',is_staff=True))
        self.semester=Semester.objects.create(name='Настройки',start_date=date(2026,9,1))
        self.group=Group.objects.create(name='251-9999',semester=self.semester)
        Student.objects.create(group=self.group,full_name='Аноним 01')
        self.pattern=WeekPattern.objects.create(semester=self.semester,name='Семь дней')
        for day in range(7):
            TimeSlot.objects.create(week_pattern=self.pattern,day_of_week=day,lesson_number=1,
                                    start_time=time(9),end_time=time(10,30))
        self.room=Room.objects.create(name='Пр1111',confirmed_capacity=None,capacity=30)
        self.profile=ResourceProfile.objects.create(key='test-resources',name='Тест',semester=self.semester,group=self.group,
                                                   rooms=[{'room_id':self.room.id,'capacity_assumption':30}])
        LessonEvent.objects.create(semester=self.semester,group=self.group,
                    teacher=Teacher.objects.create(full_name='Тестовый преподаватель'),
                    subject=Subject.objects.create(name='Предмет'),lesson_type='PRACTICE',pairs_per_week=1)
        self.payload={'semester':self.semester.id,'week_pattern':self.pattern.id,'group':self.group.id,
                      'resource_profile':self.profile.key,'clear_previous':False,
                      'settings':{'schema_version':1,'rules':{'free_days':[{'day':'sun','mode':'required'}]},
                                  'search':{'time_limit_seconds':2,'seed':17}}}

    def test_http_round_trip_freezes_inputs_and_preserves_history(self):
        legacy=GenerationRun.objects.create(semester=self.semester,status='SOLVED')
        response=self.client.post('/api/schedule/generate/',self.payload,format='json')
        self.assertEqual(response.status_code,201,response.data)
        run=GenerationRun.objects.get(pk=response.data['id'])
        self.assertEqual(run.status,'SOLVED')
        self.assertEqual(run.settings_snapshot['schema_version'],1)
        self.assertEqual(run.input_snapshot['group_id'],self.group.id)
        self.assertEqual(run.input_snapshot['rooms'][0]['confirmed_capacity'],None)
        self.assertEqual(run.input_snapshot['rooms'][0]['effective_capacity'],30)
        self.assertTrue(run.validation_snapshot['valid'])
        self.assertTrue(GenerationRun.objects.filter(pk=legacy.pk).exists())
        legacy.refresh_from_db()
        self.assertEqual(legacy.settings_snapshot,{})
        bad=self.client.post('/api/schedule/replan/',{'run':run.id,'teacher':1,'unavailable_days':[0]},format='json')
        self.assertEqual(bad.status_code,400)

    def test_strict_top_level_and_mixed_contract(self):
        for extra in [{'unsupported':True},{'seed':1},{'clear_previous':True}]:
            with self.subTest(extra=extra):
                response=self.client.post('/api/schedule/generate/',{**self.payload,**extra},format='json')
                self.assertEqual(response.status_code,400,response.data)
        self.assertEqual(GenerationRun.objects.count(),0)
