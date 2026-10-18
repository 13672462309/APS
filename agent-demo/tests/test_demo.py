import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import controller
from controller import Job, checked_config, apply_proposal, thresholds_met
from data_loader import load_dataset, default_plan, validate_plan, batch_ok
from evaluation import evaluate, rank
from agent_worker import extract_json


def fixture():
    m = dict(id='p', name='test', semi='s', diameter=2, group2='禽料', group3='g', group4='h', dosing=False, coloring=False)
    d = dict(materials={'p':m}, demands={'p':dict(wip=3, minimum=4, options=[4,8], stock=0, cover_demand=7, safety=0, name='test')},
             capacities={'p':{'1':dict(rate=4)}},
             lines={'1':dict(name='line1', workshop='W', batch=4, available_minutes=1440, previous_product='p'),
                    '2':dict(name='line2', workshop='W', batch=4, available_minutes=1440, previous_product='p')},
             prices=[dict(electricityStartTime='0',electricityEndTime='4',ePrice='.5'),dict(electricityStartTime='4',electricityEndTime='24',ePrice='1')])
    p = {'p':dict(quantity=4,priority=1)}
    raw = dict(schedule_details={'1':dict(scheduled_products=[dict(rid='p',start_time=0,end_time=105)])})
    return d,p,raw


class DemoTests(unittest.TestCase):
    def test_provided_snapshot_and_quantities(self):
        d=load_dataset(); p=default_plan(d)
        self.assertEqual(len(d['lines']),7)
        self.assertEqual(len(d['demands']),115)
        self.assertEqual(d['demands']['5082213']['wip'],63.2)
        self.assertEqual(validate_plan(d,p),[])
        self.assertTrue(all(any(batch_ok(v['quantity'],d['lines'][lid]['batch']) for lid in d['capacities'][pid]) for pid,v in p.items()))
        self.assertEqual(len(d['excluded']),4)
        # Never mutate source workbooks during reading.
        self.assertEqual(d['fingerprints'],load_dataset()['fingerprints'])

    def test_independent_metrics_and_idle_line(self):
        d,p,r=fixture();e=evaluate(d,p,r)
        self.assertTrue(e['eligible'])
        self.assertEqual(e['total_quantity'],7) # WIP included exactly once; exempt from batch rounding.
        self.assertEqual(e['metrics']['feasibility']['value'],100)
        self.assertEqual(e['metrics']['particle']['value'],None)
        self.assertEqual(e['metrics']['oee']['value'],100)
        self.assertEqual(e['metrics']['balance']['value'],0) # Idle second line is in denominator.
        self.assertEqual(e['metrics']['valley']['value'],21.88)

    def test_missing_mandatory_job_cannot_game_metrics(self):
        d,p,r=fixture();r['schedule_details']={};e=evaluate(d,p,r)
        self.assertFalse(e['eligible']);self.assertEqual(e['hard_coverage']['value'],0)
        self.assertEqual(e['metrics']['feasibility']['value'],0)

    def test_bad_duration_and_overrun_rejected_independently(self):
        d,p,r=fixture();r['schedule_details']['1']['scheduled_products'][0].update(start_time=1400,end_time=1505)
        e=evaluate(d,p,r);self.assertFalse(e['eligible']);self.assertTrue(e['violations'])
        r['schedule_details']['1']['scheduled_products'][0].update(start_time=0,end_time=1)
        self.assertFalse(evaluate(d,p,r)['eligible'])

    def test_unknown_or_duplicate_and_nan_proposals_rejected(self):
        d,p,_=fixture()
        good=dict(adjustments=[dict(product_id='p',quantity=4,priority=1)],focus='balanced',reason='x',expected_effect='y')
        self.assertEqual(apply_proposal(d,p,good),p)
        for quantity in [0,3,float('nan'),True]:
            bad=copy.deepcopy(good);bad['adjustments'][0]['quantity']=quantity
            with self.assertRaises(ValueError):apply_proposal(d,p,bad)
        bad=copy.deepcopy(good);bad['adjustments']*=2
        with self.assertRaises(ValueError):apply_proposal(d,p,bad)
        self.assertEqual(extract_json('```json\n{"a":1}\n```'),{'a':1})
        with self.assertRaises(ValueError):extract_json('done! {"a":1}')

    def test_stop_threshold_and_config_guard(self):
        d,p,r=fixture();e=evaluate(d,p,r)
        cfg=checked_config({});cfg['thresholds'].update(balance=0,valley=20)
        self.assertTrue(thresholds_met(e,cfg))
        cfg['thresholds']['valley']=99;self.assertFalse(thresholds_met(e,cfg))
        for v in [dict(max_rounds=0),dict(time_budget=True),dict(min_gain=float('inf')),dict(coverage={})]:
            with self.assertRaises(ValueError):checked_config(v)

    def test_hard_constraints_dominate_soft_score(self):
        d,p,r=fixture();good={'evaluation':evaluate(d,p,r)};bad=copy.deepcopy(good)
        bad['evaluation']['eligible']=False;bad['evaluation']['score']=100
        self.assertGreater(rank(good),rank(bad))

    def test_job_deadline_and_cancel_stop_child(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(controller,'RUNS',Path(temp)):
            j=Job(checked_config({}),'test');j.deadline=time.monotonic()-1
            with self.assertRaises(controller.Stopped):j.worker('solver_worker.py',{},1)

    def test_agent_failure_requests_cloud_stop(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(controller,'RUNS',Path(temp)):
            j=Job(checked_config({}),'test')
            process=Mock(returncode=1)
            process.communicate.return_value=(b'{"ok":false,"error":"network timeout"}',b'')
            process.poll.return_value=1
            with patch.object(controller.subprocess,'Popen',return_value=process),patch('agentcore_client.load_config',return_value=('http://test','fake-key')),patch('agentcore_client.stop_agent',return_value=True) as stop:
                with self.assertRaises(RuntimeError):j.worker('agent_worker.py',dict(thread_id='t',run_id='r'),10)
                stop.assert_called_once_with('http://test','fake-key','t','r')
                self.assertIn('云端已确认停止',j.snapshot()['events'][-1]['text'])
            j.deadline=time.monotonic()+60;j.cancel.set()
            with self.assertRaises(controller.Stopped):j.worker('solver_worker.py',{},1)

    def test_loop_keeps_best_and_stops_at_limit(self):
        d,p,r=fixture()
        d.update(warnings=[],assumptions=[],fingerprints={})
        proposals=[dict(adjustments=[],focus='balanced',reason='base',expected_effect='x'),
                   dict(adjustments=[],focus='setup',reason='try',expected_effect='y')]
        cfg=checked_config(dict(max_rounds=2,patience=2))
        with tempfile.TemporaryDirectory() as temp,patch.object(controller,'RUNS',Path(temp)),patch.object(controller,'load_dataset',return_value=d),patch.object(controller,'default_plan',return_value=p),patch.object(controller,'context_for',return_value={}):
            j=Job(cfg,'test')
            responses=[dict(proposal=proposals[0],run_id='1'),r,dict(proposal=proposals[1],run_id='2'),r]
            with patch.object(j,'worker',side_effect=responses):j.run()
            v=j.snapshot();self.assertEqual(v['status'],'completed');self.assertEqual(v['best_round'],1)
            self.assertEqual(len(v['rounds']),2);self.assertIn('最大',v['stop_reason'])
            self.assertTrue((j.folder/'snapshot.json').exists())

    def test_loop_duplicate_never_resolves_again(self):
        d,p,r=fixture();d.update(warnings=[],assumptions=[],fingerprints={})
        proposal=dict(adjustments=[],focus='balanced',reason='x',expected_effect='y')
        with tempfile.TemporaryDirectory() as temp,patch.object(controller,'RUNS',Path(temp)),patch.object(controller,'load_dataset',return_value=d),patch.object(controller,'default_plan',return_value=p),patch.object(controller,'context_for',return_value={}):
            j=Job(checked_config({}),'test')
            with patch.object(j,'worker',side_effect=[dict(proposal=proposal,run_id='1'),r,dict(proposal=proposal,run_id='2')]) as worker:j.run()
            self.assertEqual(worker.call_count,3);self.assertIn('重复',j.snapshot()['stop_reason'])


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        with patch.object(controller,'RUNS',Path(cls.temp.name)):
            import app
        cls.app=app

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_local_routes_and_secret_files(self):
        c=self.app.app.test_client()
        with c.get('/') as response:
            self.assertEqual(response.status_code,200)
        self.assertEqual(c.get('/.env').status_code,404)
        self.assertEqual(c.get('/api/jobs/not-a-valid-id').status_code,404)
        self.assertEqual(c.get('/api/bootstrap',headers={'Host':'evil.example'}).status_code,403)
        self.assertEqual(c.post('/api/jobs',json={},headers={'Origin':'http://evil.example'}).status_code,403)
        self.assertEqual(c.post('/api/jobs',json={'config':{'max_rounds':0}}).status_code,400)

    def test_export_and_history_survive_new_manager(self):
        d,p,r=fixture();e=evaluate(d,p,r)
        with tempfile.TemporaryDirectory() as temp,patch.object(controller,'RUNS',Path(temp)):
            manager=controller.Manager();job=Job(checked_config({}),'test');job.update(status='completed',best_round=1,rounds=[dict(number=1,evaluation=e)])
            recovered=controller.Manager();self.assertEqual(recovered.get(job.value['id'])['best_round'],1)
            with patch.object(self.app,'manager',recovered):
                c=self.app.app.test_client();resp=c.get('/api/jobs/'+job.value['id']+'/export/csv')
                self.assertEqual(resp.status_code,200);self.assertIn('text/csv',resp.content_type)
                self.assertIn('新增吨数',resp.data.decode('utf-8-sig'))
                self.assertEqual(c.get('/api/jobs/'+job.value['id']+'/export/json').status_code,200)


if __name__ == '__main__':unittest.main()
