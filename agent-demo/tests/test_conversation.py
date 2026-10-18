import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import controller
import conversation
from conversation import Conversations,detect
from controller import Job,checked_config,apply_proposal
from data_loader import load_dataset,default_plan
from planning_state import checked_constraints,constrained_data,validate_revision
from evaluation import evaluate
from demand_fields import catalog, rows_for
from order_details import order_details_for
from test_demo import fixture


class ConversationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.data=load_dataset()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.patches=[patch.object(conversation,'SESSIONS',self.root/'sessions'),patch.object(conversation,'RUNS',self.root/'runs'),patch.object(controller,'RUNS',self.root/'runs')]
        for p in self.patches:p.start()
        self.manager=controller.Manager();self.chat=Conversations(self.manager)
        with patch.object(conversation,'load_dataset',return_value=copy.deepcopy(self.data)):
            self.sid=self.chat.create({'mode':'baseline'})['id']

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def seed(self):
        s=self.chat.load(self.sid)
        # A revision source with a real dataset and a synthetic evaluated placeholder.
        j=Job(checked_config({'mode':'baseline'}),'test')
        j.update(status='completed',best_round=1,rounds=[dict(number=1,plan=copy.deepcopy(s['plan']),evaluation={'eligible':True,'score':80},proposal={'reason':'test'})])
        (j.folder/'snapshot.json').write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        s.update(selected_job_id=j.value['id'],display_job_id=j.value['id'],phase=5,versions=[j.value['id']])
        self.chat.save(s)
        return j

    def test_query_and_stage_do_not_start_solver(self):
        with patch.object(self.manager,'start') as run:
            v=self.chat.message(self.sid,'查询5000611的需求')
            self.assertEqual(v['view']['rows'][0]['id'],'5000611');self.assertEqual(v['last_product'],'5000611')
            self.assertEqual(self.chat.message(self.sid,'开始排产')['phase'],0)
            self.assertEqual(self.chat.message(self.sid,'继续：供需分析')['phase'],1)
            self.assertEqual(self.chat.message(self.sid,'继续：确认排产目标')['phase'],2)
            run.assert_not_called()

    def test_back_navigation_uses_current_step_and_never_implicitly_solves(self):
        with patch.object(self.manager,'start') as run:
            self.chat.message(self.sid,'开始排产')
            v=self.chat.message(self.sid,action={'type':'next_stage','phase':2})
            self.assertEqual(v['current_stage'],0)
            self.chat.message(self.sid,'继续')
            self.chat.message(self.sid,'继续')
            v=self.chat.message(self.sid,'返回数据校验')
            self.assertEqual((v['phase'],v['current_stage'],v['view']['type']),(2,0,'validation'))
            v=self.chat.message(self.sid,'继续')
            self.assertEqual((v['phase'],v['current_stage'],v['view']['type']),(2,1,'demand'))
            v=self.chat.message(self.sid,action={'type':'next_stage','phase':2})
            self.assertEqual(v['current_stage'],2)
            v=self.chat.message(self.sid,'继续')
            self.assertEqual(v['current_stage'],2);self.assertFalse(v['active_job_id'])
            v=self.chat.message(self.sid,'上一步')
            self.assertEqual(v['current_stage'],1)
            recovered=Conversations(self.manager).get(self.sid)
            self.assertEqual(recovered['current_stage'],1)
            self.assertEqual(recovered['phase'],2)
            # A previously reached demand page is also accessible from the sidebar.
            self.chat.message(self.sid,action={'type':'stage','phase':0})
            v=self.chat.message(self.sid,action={'type':'next_stage','phase':2})
            self.assertEqual(v['current_stage'],2)
            run.assert_not_called()

    def test_detail_return_full_table_does_not_advance_workflow(self):
        self.chat.message(self.sid,'开始排产');self.chat.message(self.sid,'继续')
        v=self.chat.message(self.sid,'查询5000611的需求')
        self.assertEqual(len(v['view']['rows']),1);self.assertTrue(v['view']['is_detail'])
        v=self.chat.message(self.sid,action={'type':'query','all':True})
        self.assertEqual(len(v['view']['rows']),115);self.assertFalse(v['view']['is_detail'])
        self.assertEqual(v['current_stage'],1);self.assertEqual(v['last_product'],'5000611')

    def test_reference_fields_keep_missing_data_and_exact_excel_sources(self):
        fields=catalog()
        self.assertEqual(len(fields),81);self.assertEqual(len({f['key'] for f in fields}),81)
        self.assertEqual(fields[0]['label'],'订单需求量');self.assertEqual(fields[-1]['label'],'下周期需求量')
        rows={r['id']:r['fields'] for r in self.chat.get(self.sid)['products']}
        r=rows['5000469']
        self.assertEqual(r['sales_7_daily'],.34)
        self.assertAlmostEqual(r['sales_7_total'],2.38)
        self.assertEqual(r['day_demand_0'],2.4)
        self.assertEqual(r['day_demand_1'],0)
        self.assertIsNone(r['due_date']);self.assertIsNone(r['frozen_stock']);self.assertIsNone(r['month_actual'])
        self.assertEqual(rows['5082213']['order_ids'],'106732936')
        self.assertEqual(rows['5082213']['wip'],63.2)
        self.assertIsNone(rows['5082213']['plan_quantity'])
        self.assertTrue(all(r['plan_quantity'] is None and r['decomposed_quantity'] is None and r['suggested_quantity'] is None for r in rows.values()))
        # Five-day coverage needs ten demand days for a complete next period; only eight exist.
        self.assertIsNone(rows['5000613']['next_period_demand'])

    def test_display_metadata_migration_preserves_old_plan(self):
        s=self.chat.load(self.sid);s['data'].pop('presentation_version');s['data'].pop('wip_orders')
        for d in s['data']['demands'].values():d.pop('avg_sales_7');d.pop('avg_sales_30')
        before=copy.deepcopy(s['plan']);self.chat.save(s)
        v=Conversations(self.manager).get(self.sid)
        r=next(r for r in v['products'] if r['id']=='5000469')
        self.assertEqual(r['fields']['sales_7_daily'],.34)
        self.assertEqual({r['id']:r['quantity'] for r in v['products']},{p:v['quantity'] for p,v in before.items()})

    def test_modify_then_pronoun_and_persistence(self):
        self.seed();self.chat.message(self.sid,'查询5000611的需求')
        v=self.chat.message(self.sid,'把它的总产量改成33吨')
        self.assertEqual(v['constraints']['quantities']['5000611'],33)
        self.assertTrue(v['dirty']);self.assertEqual(v['job']['rounds'][0]['plan']['5000611']['quantity'],30.8)
        recovered=Conversations(self.manager).get(self.sid)
        self.assertEqual(recovered['constraints']['quantities']['5000611'],33)
        self.assertEqual(recovered['messages'],v['messages'])

    def test_manual_decomposition_before_solving_aggregates_and_persists(self):
        with patch.object(self.manager,'start') as start:
            v=self.chat.message(self.sid,action={'type':'edit','changes':[
                {'type':'quantity','product':'5000611','quantity':33},
                {'type':'quantity','product':'5082213','quantity':63.2}]})
            rows={r['id']:r['fields'] for r in v['products']}
            self.assertEqual(rows['5000611']['decomposed_quantity'],33)
            self.assertEqual(rows['5000611']['plan_quantity'],33)
            self.assertEqual(rows['5082213']['plan_quantity'],63.2)
            self.assertEqual(v['constraints']['quantities']['5082213'],0)
            self.assertIsNone(rows['5000469']['plan_quantity'])
            self.assertEqual(v['current_stage'],1)
            self.assertFalse(v['versions']);start.assert_not_called()
            recovered=Conversations(self.manager).get(self.sid)
            self.assertEqual(next(r for r in recovered['products'] if r['id']=='5000611')['fields']['plan_quantity'],33)
            v=self.chat.message(self.sid,action={'type':'config','patch':{'coverage':{'常规1':3}}})
            self.assertEqual(v['constraints']['quantities']['5000611'],33)
            self.assertEqual(next(r for r in v['products'] if r['id']=='5000611')['fields']['plan_quantity'],33)

    def test_rejected_initial_decomposition_does_not_fill_any_rows(self):
        v=self.chat.message(self.sid,action={'type':'edit','changes':[
            {'type':'quantity','product':'5000611','quantity':33},
            {'type':'quantity','product':'5082213','quantity':60}]})
        self.assertIn('未应用',v['messages'][-1]['text'])
        self.assertFalse(v['constraints']['quantities'])
        self.assertTrue(all(r['fields']['plan_quantity'] is None for r in v['products']))

    def test_order_details_aggregate_lines_keep_unscheduled_and_unknown_orders(self):
        data={'demands':{'a':{'name':'物料甲','category':'常规1','wip':2},'b':{'name':'物料乙','category':'非常规2','wip':0}}}
        result={'plan':{'a':{'quantity':8},'b':{'quantity':5}},'evaluation':{'rows':[
            {'product_id':'a','quantity':4,'line_name':'一号线','start':0,'end':30,'valid':True},
            {'product_id':'a','quantity':6,'line_name':'二号线','start':30,'end':70,'valid':True}]}}
        rows=order_details_for(data,result)
        self.assertEqual(rows[0]['quantity'],10)
        self.assertEqual(sum(r['quantity'] for r in rows[0]['decomposition_rows']),10)
        self.assertEqual(rows[0]['lines'],['一号线','二号线'])
        self.assertEqual(rows[0]['status'],'已排入');self.assertEqual(rows[1]['status'],'未排入')
        self.assertTrue(all(r['order_number'] is None and r['delivery'] is None for r in rows))
        result['evaluation']['rows'].pop()
        self.assertEqual(order_details_for(data,result)[0]['status'],'部分排入')
        result['evaluation']['rows'][0]['valid']=False
        self.assertEqual(order_details_for(data,result)[0]['status'],'待检查')

    def test_invalid_atomic_batch_and_wip_protected(self):
        self.seed();before=copy.deepcopy(self.chat.load(self.sid)['plan'])
        v=self.chat.message(self.sid,action={'type':'edit','changes':[{'type':'quantity','product':'5000611','quantity':33},{'type':'quantity','product':'5082213','quantity':60}]})
        self.assertEqual(self.chat.load(self.sid)['plan'],before)
        self.assertFalse(v['constraints']['quantities']);self.assertIn('未应用',v['messages'][-1]['text'])
        v=self.chat.message(self.sid,'把5000611的产量改成31吨')
        self.assertIn('整批',v['messages'][-1]['text']);self.assertEqual(self.chat.load(self.sid)['plan'],before)

    def test_read_only_explanation_cannot_be_placement(self):
        self.assertEqual(detect('为什么5000611安排在二号线',{})['type'],'explain')
        a=detect('5000611安排在二号线，08:00开始',{})
        self.assertEqual(a['placement'],{'line_id':'2','start_minute':720})
        for text in ('不要把5000611改成33吨','如果把5000611改成33吨会怎样','5000611改成33吨同时放二号线','二号线08:00到10:00停用'):
            self.assertEqual(detect(text,{})['type'],'help')

    def test_session_api_history_and_resume(self):
        import app
        with patch.object(app,'conversations',self.chat):
            client=app.app.test_client()
            response=client.post(f'/api/sessions/{self.sid}/messages',json={'text':'查询5000611的需求'})
            self.assertEqual(response.status_code,200)
            history=client.get('/api/sessions').get_json()
            self.assertEqual(history[0]['id'],self.sid)
            self.assertEqual(history[0]['messages'],3)
            recovered=client.get('/api/sessions/'+self.sid).get_json()
            self.assertEqual(recovered['last_product'],'5000611')
            self.assertEqual(len(recovered['products'][0]['daily']),8)

    def test_failed_revision_keeps_parent_and_pending_edits(self):
        parent=self.seed();self.chat.message(self.sid,'5000611改成33吨')
        s=self.chat.load(self.sid)
        failure=Job(checked_config({}),'invalid result')
        failure.update(status='completed',best_round=1,rounds=[dict(number=1,plan=copy.deepcopy(s['plan']),evaluation={'eligible':False,'score':90})],stop_reason='未满足硬约束')
        s['active_job_id']=failure.value['id'];s['display_job_id']=failure.value['id'];s['versions'].append(failure.value['id'])
        self.chat.save(s)
        v=self.chat.get(self.sid)
        self.assertEqual(v['selected_job_id'],parent.value['id'])
        self.assertTrue(v['dirty']);self.assertEqual(v['constraints']['quantities']['5000611'],33)
        self.assertIn('保留此前版本',v['messages'][-1]['text'])

    def test_unknown_material_is_not_silently_all_products(self):
        v=self.chat.message(self.sid,'查询不存在的物料的需求')
        self.assertIn('未找到',v['messages'][-1]['text'])
        v=self.chat.message(self.sid,'先看一下需求概况')
        self.assertEqual(len(v['view']['rows']),len(self.data['demands']))

    def test_explanation_uses_requested_round_and_frozen_facts(self):
        j=self.seed();first=j.value['rounds'][0]
        first['evaluation'].update(rows=[],violations=[])
        second=copy.deepcopy(first);second['number']=2;second['plan']['5000611']['quantity']=33
        j.update(rounds=[first,second])
        # A future draft coverage change must not alter the explanation of an old run.
        s=self.chat.load(self.sid);s['data']['demands']['5000611']['cover_demand']=999
        v=self.chat.message(self.sid,action={'type':'explain','product':'5000611','round':2,'job_id':j.value['id']})
        text=v['messages'][-1]['text']
        self.assertIn('第 2 轮',text);self.assertIn('本方案新增 33 吨',text)
        self.assertIn('覆盖需求 24',text);self.assertNotIn('999',text)

    def test_constraints_reject_illegal_line_nan_and_threshold(self):
        self.seed();v=self.chat.message(self.sid,action={'type':'placement','product':'5000611','placement':{'line_id':'7'}})
        self.assertIn('未应用',v['messages'][-1]['text']);self.assertFalse(v['constraints']['placements'])
        v=self.chat.message(self.sid,action={'type':'quantity','product':'5000611','quantity':float('nan')})
        self.assertIn('未应用',v['messages'][-1]['text'])
        v=self.chat.message(self.sid,'及时交付目标改为92%')
        self.assertEqual(v['config']['thresholds']['delivery'],100)
        v=self.chat.message(self.sid,'OEE目标改为88%')
        self.assertEqual(v['config']['thresholds']['oee'],88)

    def test_agent_cannot_change_locked_quantity(self):
        data=constrained_data(self.data,{'quantities':{'5000611':33}})
        plan=default_plan(self.data);plan['5000611']['quantity']=33
        proposal={'adjustments':[{'product_id':'5000611','quantity':30.8,'priority':2}],'focus':'balanced','reason':'test','expected_effect':'test'}
        with self.assertRaises(ValueError):apply_proposal(data,plan,proposal)

    def test_unavailable_publish_is_explicit(self):
        v=self.chat.message(self.sid,'下达 APS')
        self.assertIn('尚未接入',v['messages'][-1]['text'])
        self.assertFalse(v['versions'])

    def test_unknown_language_uses_cloud_interpreter_and_local_guard(self):
        self.seed()
        with patch.object(conversation,'interpret',return_value={'type':'quantity','product':'5000611','quantity':31}) as nlp:
            v=self.chat.message(self.sid,'我想让这个产品用另一种方式生产')
            nlp.assert_called_once();self.assertIn('未应用',v['messages'][-1]['text'])

    def test_revision_first_solves_seed_then_agent_preserving_locks(self):
        d,p,r=fixture();d.update(warnings=[],assumptions=[],fingerprints={},coverage={})
        constraints=checked_constraints(d,{'quantities':{'p':4}})
        cfg=checked_config({'max_rounds':2,'patience':2})
        revision={'data':d,'plan':p,'constraints':constraints,'parent_job_id':'parent','session_id':self.sid}
        proposal=dict(adjustments=[],focus='setup',reason='test',expected_effect='test')
        j=Job(cfg,'test',revision)
        with patch.object(controller,'context_for',return_value={}),patch.object(j,'worker',side_effect=[r,{'proposal':proposal,'run_id':'test'},r]) as worker:
            j.run()
        self.assertEqual([c.args[0] for c in worker.call_args_list],['solver_worker.py','agent_worker.py','solver_worker.py'])
        self.assertEqual(j.value['parent_job_id'],'parent')
        self.assertEqual(j.value['best_round'],1)
        self.assertEqual(j.value['rounds'][1]['plan']['p']['quantity'],4)

    def test_independent_validator_rejects_ignored_placement(self):
        d,p,r=fixture();d['user_constraints']={'placements':{'p':{'start_minute':60}}}
        self.assertFalse(evaluate(d,p,r)['eligible'])
        d['user_constraints']={'disabled_lines':['1']}
        self.assertFalse(evaluate(d,p,r)['eligible'])


if __name__=='__main__':unittest.main()
