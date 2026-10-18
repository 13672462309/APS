from flask import Flask, request, jsonify
from flask_restx import Api
from http_util import HpaasApiClient
import jsonschema
import constant
import traceback
import uuid
from logging_config import setup_logging, get_logger

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
api = Api(app, version='1.0', description='APS排产排程', doc=False)

# 初始化日志系统
setup_logging(app)  # 初始化全局logger
logger = get_logger()  # 获取logger实例

# 在app初始化后添加请求前处理器
@app.before_request
def before_request():
    request_id = request.headers.get('X-Request-ID')
    if not request_id:
        request_id = str(uuid.uuid4())
    request.request_id = request_id


def send_json_response(code, msg, data=None, status_code=200):
    """
    返回JSON格式的响应
    """
    response = {'code': code, 'message': msg}
    if data is not None:
        response['data'] = data
    return jsonify(response), status_code


@app.route('/test', methods=['GET'])
def test():
    """
    测试接口
    :return: 返回JSON格式的响应消息
    """
    return send_json_response('000', 'hello world')


def _get_hpaas_client():
    """获取/复用 HPAAS API 客户端单例"""
    if not hasattr(_get_hpaas_client, '_instance'):
        env = "prod" if constant.IS_PROD else "uat"
        cfg = constant.hpaas_config[env]
        _get_hpaas_client._instance = HpaasApiClient(
            cfg["token_url"], cfg["client_id"], cfg["client_secret"])
        logger.info(f"HPAAS 客户端初始化完成 (env={env})")
    return _get_hpaas_client._instance


def _calculate_weights_v2(scheduling_strategy):
    """v2 接口：根据 strategy 自动计算规则权重。

    formula: weight = base × goal_multiplier × priority_factor
      - goal_multiplier: 核心目标=1.0, 非核心=0.5
        特例: production_balance (PR0010 产线均衡) 不因非核心打折 (恒 1.0)。
        产线均衡是全局结构性目标, 非"核心/非核心"二分能衡量: 任何情况下都应
        保证各产线完工时刻/负载尽量趋同, 否则 CX02 饿死、CX05 堆爆这类不均
        衡会反复出现。权重扫描证明 load-balance 需 w≥200 才压住极差, 非核心
        打折会把 pb 系统性压到 20 (失效区间), 故对此规则豁免 goal_mul=0.5。
      - priority_factor: 指数衰减 WEIGHT_PRIORITY_BASE ^ (-priority)
        (priority 0→1→2→3 每级严格 ÷2，确保高 priority 规则权重
         永远压过低 priority，不被 base 差异掩盖；详见 constant.py)
      - electricity_cost 特殊处理: 核心=1, 非核心=0
    未出现的规则权重保持 0。
    """
    weights = dict(constant.constraint_weights)  # 全部 0 起点

    for s in scheduling_strategy:
        rule_code = s.get('ruleCode', '')
        constraint_type = constant.rule_constraint_mapping.get(rule_code)
        if not constraint_type:
            logger.warning(f"未知 ruleCode: {rule_code}, 跳过")
            continue

        is_primary = int(s.get('isPrimaryGoal', 0)) == 1
        priority = float(s.get('rulePriority', 1))

        base = constant.base_weights.get(constraint_type, 0)
        # production_balance 是全局均衡目标, 不受 isPrimaryGoal 非核心打折
        # (详见函数 docstring); 其余规则按核心/非核心区分。
        if constraint_type == 'production_balance':
            goal_mul = constant.WEIGHT_GOAL_MULTIPLIER_PRIMARY
        else:
            goal_mul = (
                constant.WEIGHT_GOAL_MULTIPLIER_PRIMARY if is_primary
                else constant.WEIGHT_GOAL_MULTIPLIER_SECONDARY
            )
        # 指数衰减: 每提升一级 priority, factor ÷WEIGHT_PRIORITY_BASE, 区分度严格
        pri_factor = max(
            constant.WEIGHT_PRIORITY_FLOOR,
            constant.WEIGHT_PRIORITY_BASE ** (-priority),
        )

        if constraint_type == 'electricity_cost':
            weights[constraint_type] = 1 if is_primary else 0
        else:
            weights[constraint_type] = int(base * goal_mul * pri_factor)

    logger.info(f"v2 权重计算结果: {weights}")
    return weights


def _get_hpaas_result_url():
    env = "prod" if constant.IS_PROD else "uat"
    return constant.hpaas_config[env]["result_url"]


def _respond_with_schedule(post_data, aps, lines_scheduled):
    """返回完整试算结果；回写状态独立报告，回写失败不丢失计算结果。"""
    trace_id = post_data['traceId']
    version_id = post_data['versionId']
    # 内部 scheduled 是 set，HTTP JSON 使用稳定排序的数组，不修改求解结果。
    solver_result = dict(lines_scheduled)
    solver_result['scheduled'] = sorted(lines_scheduled.get('scheduled', []))
    solver_result['unscheduled'] = sorted(lines_scheduled.get('unscheduled', []))
    schedule_results = aps.build_schedule_results(lines_scheduled)
    callback_payload = {
        'traceId': trace_id,
        'versionId': version_id,
        'scheduleResults': schedule_results,
    }

    write_back = {'status': 'skipped'}
    if post_data.get('writeBack', True):
        try:
            hpaas = _get_hpaas_client()
            resp = hpaas.post_json(_get_hpaas_result_url(), callback_payload, timeout=30)
            if resp.get('success'):
                write_back = {'status': 'success'}
                logger.info(f'HPAAS 回写成功: traceId={trace_id}')
            else:
                write_back = {'status': 'failed', 'statusCode': resp.get('status_code')}
                logger.error(f'HPAAS 回写失败: traceId={trace_id}, status={resp.get("status_code")}')
        except Exception:
            write_back = {'status': 'failed'}
            logger.exception(f'HPAAS 回写异常: traceId={trace_id}')

    message = '排程完成，业务回写失败' if write_back['status'] == 'failed' else '排程完成'
    return send_json_response('000', message, {
        **callback_payload,
        'solverResult': solver_result,
        'writeBack': write_back,
    })


@app.route('/submit-task', methods=['POST'])
def submit_task():
    """
    提交排产任务：返回完整结果，默认回写 HPAAS；writeBack=false 时仅试算。
    """
    try:
        post_data = request.get_json()
        jsonschema.validate(instance=post_data, schema=constant.schema)
    except jsonschema.SchemaError as e:
        logger.info(f"模式错误: {str(e)}")
        return send_json_response('202', f"模式错误, {str(e.message)}")
    except jsonschema.ValidationError as e:
        logger.info(f"验证失败: {str(e)}")
        return send_json_response('203', f"验证失败, {str(e.message)}")

    trace_id = post_data['traceId']
    version_id = post_data['versionId']
    logger.info(f"收到任务提交请求: traceId={trace_id}, versionId={version_id}")

    try:
        from aps import Aps
        scheduling_strategy = post_data.get('schedulingStrategy', [])

        aps = Aps(
            post_data['productionPlan'],
            post_data['productionLine'],
            post_data['switchMatrix'],
            version_id,
            trace_id,
            post_data['orgId'],
            scheduling_strategy,
        )
        lines_scheduled = aps.production_scheduling()

        if not lines_scheduled:
            logger.warning(f"排产无结果, traceId={trace_id}")
            return send_json_response('500', '排产无结果', {'traceId': trace_id})

        scores = lines_scheduled.get('scores', {})
        scheduled_count = len(lines_scheduled.get('scheduled', set()))
        unscheduled_count = len(lines_scheduled.get('unscheduled', []))
        logger.info(f"排产完成: 已排={scheduled_count}, 未排={unscheduled_count}, 业务综合分={scores.get('total_score', 0):.2f}")

        return _respond_with_schedule(post_data, aps, lines_scheduled)

    except Exception:
        logger.error(f"排产处理错误: {traceback.format_exc()} traceId={trace_id}")
        return send_json_response('500', '排产处理失败', {'traceId': trace_id})


@app.route('/submit-task-v2', methods=['POST'])
def submit_task_v2():
    """
    提交排产任务 (v2)：根据 schedulingStrategy 的 isPrimaryGoal 和 rulePriority
    自动计算权重，无需前端传入 ruleWeight。

    schedulingStrategy 新格式:
      [{"ruleCode": "PR0009", "rulePriority": "1", "isPrimaryGoal": "1"}, ...]

    约束权重完全由 schedulingStrategy 的 isPrimaryGoal/rulePriority 自动计算，
    与历史 totalStrategy 字段无关（该机制已废弃）。
    """
    try:
        post_data = request.get_json()
        jsonschema.validate(instance=post_data, schema=constant.schema)
    except jsonschema.SchemaError as e:
        logger.info(f"模式错误: {str(e)}")
        return send_json_response('202', f"模式错误, {str(e.message)}")
    except jsonschema.ValidationError as e:
        logger.info(f"验证失败: {str(e)}")
        return send_json_response('203', f"验证失败, {str(e.message)}")

    trace_id = post_data['traceId']
    version_id = post_data['versionId']
    logger.info(f"收到 v2 任务提交请求: traceId={trace_id}, versionId={version_id}")

    try:
        from aps import Aps
        scheduling_strategy = post_data.get('schedulingStrategy', [])

        aps = Aps(
            post_data['productionPlan'],
            post_data['productionLine'],
            post_data['switchMatrix'],
            version_id,
            trace_id,
            post_data['orgId'],
            scheduling_strategy,
        )

        # v2 核心: 根据 strategy 自动计算权重，覆盖 Aps 中从 ruleWeight 读取的逻辑
        v2_weights = _calculate_weights_v2(scheduling_strategy)
        # 用 set_constraint_weights 同步 self 与 ctx, 避免 production_scheduling 走 ctx 读到旧权重
        aps.set_constraint_weights(v2_weights)
        logger.info(f"v2 约束权重已注入: {v2_weights}")

        lines_scheduled = aps.production_scheduling()

        if not lines_scheduled:
            logger.warning(f"排产无结果, traceId={trace_id}")
            return send_json_response('500', '排产无结果', {'traceId': trace_id})

        scores = lines_scheduled.get('scores', {})
        scheduled_count = len(lines_scheduled.get('scheduled', set()))
        unscheduled_count = len(lines_scheduled.get('unscheduled', []))
        logger.info(f"排产完成: 已排={scheduled_count}, 未排={unscheduled_count}, 业务综合分={scores.get('total_score', 0):.2f}")

        return _respond_with_schedule(post_data, aps, lines_scheduled)

    except Exception:
        logger.error(f"排产处理错误: {traceback.format_exc()} traceId={trace_id}")
        return send_json_response('500', '排产处理失败', {'traceId': trace_id})


@app.route('/estimate-task', methods=['POST'])
def estimate_task():
    """
    提交排产预估请求
    """
    try:
        post_data = request.get_json()
        # 复用submit-task的校验逻辑
        jsonschema.validate(instance=post_data, schema=constant.schema)
    except jsonschema.SchemaError as e:
        logger.info(f"模式错误: {str(e)}")
        return send_json_response('202', f"模式错误, {str(e.message)}")
    except jsonschema.ValidationError as e:
        logger.info(f"验证失败: {str(e)}")
        return send_json_response('203', f"验证失败, {str(e.message)}")

    logger.info(f"收到排产预估请求: {post_data.get('traceId', 'unknown')}")
    from aps import Aps
    
    try:
        # 获取调度策略
        scheduling_strategy = post_data.get('schedulingStrategy', [])

        aps = Aps(
            post_data['productionPlan'],
            post_data['productionLine'],
            post_data['switchMatrix'],
            post_data['versionId'],
            post_data['traceId'],
            post_data['orgId'],
            scheduling_strategy,
        )
        
        estimation_result = aps.estimate_production_load()
        
        if estimation_result is None:
             return send_json_response('500', '预估计算失败(可能是数据库连接问题)')
             
        return send_json_response('000', 'Success', estimation_result)

    except Exception as e:
        logger.error(f"预估处理错误: {traceback.format_exc()}")
        return send_json_response('500', f"预估处理异常: {str(e)}")



if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8123, use_reloader=False)
