"""求解接口返回契约：完整 JSON、试算不回写、失败不伪报成功。"""
import copy
import os
import sys
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, REPO_ROOT)

import aps_feed


def make_request():
    plan = {
        'recordId': '1001', 'productId': 'P1', 'productName': '产品一',
        'semiFinishedProductId': 'S1', 'semiFinishedProductName': '半成品一',
        'thirdMaterialGroupId': 'G1', 'thirdMaterialGroupDesc': '组一',
        'forthMaterialGroupId': 'G2', 'forthMaterialGroupDesc': '组二',
        'productionParticleSize': '3.0', 'aspectRatio': '', 'dia': '3.0',
        'packageType': '01', 'materialPriority': '1',
        'plannedProductionQuantity': 10, 'earliestStartTime': '0',
        'latestCompletionTime': '24', 'proteinGradeLevel': '0',
    }
    line = {key: value for key, value in plan.items() if key not in {
        'materialPriority', 'earliestStartTime', 'latestCompletionTime',
        'proteinGradeLevel',
    }}
    line.update({
        'productionLineId': 'L1', 'productionLineName': '产线一',
        'productionLineDescription': '', 'lineRemainingTime': 24,
        'recordId': '1000', 'productId': 'PRE', 'plannedProductionQuantity': 0,
    })
    return {
        'orgId': '1', 'traceId': 'test-trace', 'versionId': 'test-version',
        'strategyId': 'test-strategy', 'productionPlan': [plan],
        'productionLine': [line],
        'switchMatrix': {'1000': {'1001': '5'}, '1001': {'1000': '5'}},
        'schedulingStrategy': [
            {'ruleCode': 'PR0009', 'rulePriority': '1', 'isPrimaryGoal': '1'},
        ],
    }


def make_result():
    return {
        'L1': {1: '1001'}, 'scheduled': {'1001'}, 'unscheduled': ['1003', '1002'],
        'time': 65, 'overall_utilization': 4.51,
        'schedule_details': {'L1': {
            'scheduled_products': [{
                'rid': '1001', 'start_time': 5, 'end_time': 65,
                'production_time': 60, 'switch_time': 5,
            }],
            'time_slots': [{'type': 'switch', 'start_time': 0, 'end_time': 5}],
        }},
        'scores': {'total_score': 100, 'electricity_cost': 0},
    }


class TestSubmitTaskApi(unittest.TestCase):
    endpoints = ('/submit-task', '/submit-task-v2')

    def setUp(self):
        self.client = aps_feed.app.test_client()
        self.request = make_request()
        self.result = make_result()
        self.schedule_results = [{'recordId': '1001', 'productQuantity': '10'}]
        self.aps_patch = patch('aps.Aps')
        self.aps_class = self.aps_patch.start()
        self.addCleanup(self.aps_patch.stop)
        self.aps = self.aps_class.return_value
        self.aps.production_scheduling.return_value = self.result
        self.aps.build_schedule_results.return_value = self.schedule_results
        self.client_patch = patch('aps_feed._get_hpaas_client')
        self.hpaas_factory = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.hpaas_factory.return_value.post_json.return_value = {'success': True}

    def post(self, endpoint):
        response = self.client.post(endpoint, json=self.request)
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_returns_full_json_and_preserves_default_callback(self):
        for endpoint in self.endpoints:
            with self.subTest(endpoint=endpoint):
                self.hpaas_factory.reset_mock()
                body = self.post(endpoint)
                self.assertEqual(body['code'], '000')
                data = body['data']
                self.assertEqual(data['traceId'], 'test-trace')
                self.assertEqual(data['versionId'], 'test-version')
                self.assertEqual(data['scheduleResults'], self.schedule_results)
                expected = copy.deepcopy(self.result)
                expected['scheduled'] = ['1001']
                expected['unscheduled'] = ['1002', '1003']
                expected['L1'] = {'1': '1001'}  # JSON 对象键是字符串。
                self.assertEqual(data['solverResult'], expected)
                self.assertEqual(data['writeBack'], {'status': 'success'})
                self.hpaas_factory.assert_called_once_with()
                callback = self.hpaas_factory.return_value.post_json.call_args
                self.assertEqual(callback.args[1], {
                    'traceId': 'test-trace', 'versionId': 'test-version',
                    'scheduleResults': self.schedule_results,
                })
        self.assertEqual(self.result['scheduled'], {'1001'})
        self.assertEqual(self.result['unscheduled'], ['1003', '1002'])

    def test_dry_run_never_initializes_callback_client(self):
        self.request['writeBack'] = False
        for endpoint in self.endpoints:
            with self.subTest(endpoint=endpoint):
                data = self.post(endpoint)['data']
                self.assertEqual(data['writeBack'], {'status': 'skipped'})
                self.assertIn('schedule_details', data['solverResult'])
        self.hpaas_factory.assert_not_called()

    def test_callback_failure_preserves_result(self):
        self.hpaas_factory.return_value.post_json.return_value = {
            'success': False, 'status_code': 503,
        }
        for endpoint in self.endpoints:
            with self.subTest(endpoint=endpoint):
                body = self.post(endpoint)
                self.assertEqual(body['code'], '000')
                self.assertEqual(body['data']['writeBack'], {
                    'status': 'failed', 'statusCode': 503,
                })
                self.assertEqual(body['data']['solverResult']['scheduled'], ['1001'])

    def test_callback_exception_preserves_result(self):
        self.hpaas_factory.side_effect = RuntimeError('callback unavailable')
        for endpoint in self.endpoints:
            with self.subTest(endpoint=endpoint):
                body = self.post(endpoint)
                self.assertEqual(body['code'], '000')
                self.assertEqual(body['data']['writeBack'], {'status': 'failed'})
                self.assertIn('schedule_details', body['data']['solverResult'])

    def test_solver_failure_is_not_accepted(self):
        self.aps.production_scheduling.side_effect = RuntimeError('solver failed')
        for endpoint in self.endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.post(endpoint)['code'], '500')
        self.hpaas_factory.assert_not_called()

    def test_result_conversion_failure_is_not_accepted(self):
        self.aps.build_schedule_results.side_effect = RuntimeError('conversion failed')
        for endpoint in self.endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.post(endpoint)['code'], '500')
        self.hpaas_factory.assert_not_called()

    def test_no_solution_is_not_accepted(self):
        self.aps.production_scheduling.return_value = {}
        for endpoint in self.endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.post(endpoint)['code'], '500')
        self.hpaas_factory.assert_not_called()

    def test_write_back_must_be_boolean(self):
        self.request['writeBack'] = 'false'
        for endpoint in self.endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.post(endpoint)['code'], '203')
        self.aps_class.assert_not_called()
        self.hpaas_factory.assert_not_called()

    def test_v2_still_sets_calculated_weights(self):
        self.request['writeBack'] = False
        self.post('/submit-task-v2')
        weights = self.aps.set_constraint_weights.call_args.args[0]
        self.assertEqual(weights['changeover_time'], 100)
        self.assertEqual(weights['production_balance'], 0)


class TestSubmitTaskResultBuilder(unittest.TestCase):
    def test_real_result_builder_returns_times_without_network(self):
        request_data = make_request()
        request_data['writeBack'] = False

        def build_offline_result(ctx):
            from scheduler.result_builder import build_result_from_assignment
            ctx.product_product_line_capacity_matrix = {
                'P1-L1': {'capacity': 10, 'priority': 0, 'min_quantity': 0},
            }
            ctx.prod_time_matrix = {('1000', 'L1'): (0, 0), ('1001', 'L1'): (60, 0)}
            ctx.switch_time_matrix = {('1000', '1001'): 5}
            return build_result_from_assignment(
                ctx, {'L1': ['1001']}, {'L1': 1440}, 5, {'1001'},
                line_times={'L1': {'1001': (5, 65)}},
            )

        with (
            patch('scheduler.model_build.schedule', side_effect=build_offline_result),
            patch('aps_feed._get_hpaas_client') as callback,
            patch('scheduler.capacity.get_product_capacity_list') as database,
        ):
            for endpoint in ('/submit-task', '/submit-task-v2'):
                with self.subTest(endpoint=endpoint):
                    response = aps_feed.app.test_client().post(endpoint, json=request_data)
                    body = response.get_json()
                    self.assertEqual(body['code'], '000', body)
                    data = body['data']
                    self.assertEqual(data['solverResult']['scheduled'], ['1001'])
                    self.assertEqual(data['solverResult']['unscheduled'], [])
                    slot = data['solverResult']['schedule_details']['L1']['scheduled_products'][0]
                    self.assertGreaterEqual(slot['start_time'], 5)
                    self.assertEqual(slot['end_time'] - slot['start_time'], 60)
                    self.assertEqual(data['scheduleResults'][0]['productQuantity'], '10.0')
                    self.assertEqual(data['writeBack'], {'status': 'skipped'})
            callback.assert_not_called()
            database.assert_not_called()


if __name__ == '__main__':
    unittest.main()
