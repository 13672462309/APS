"""User revisions are explicit constraints, never untrusted prompt-only requests."""
import copy
import math
from data_loader import validate_plan, batch_ok

FOCUSES = {'balanced': '产线均衡', 'setup': '减少换型', 'particle': '粒径顺序', 'valley': '谷电利用', 'delivery': '保障交付'}


def checked_constraints(data, value=None):
    value = copy.deepcopy(value or {})
    if not isinstance(value, dict) or set(value) - {'quantities', 'placements', 'disabled_lines', 'focus'}:
        raise ValueError('不支持的约束字段。')
    value.setdefault('quantities', {})
    value.setdefault('placements', {})
    value.setdefault('disabled_lines', [])
    value.setdefault('focus', None)
    if value['focus'] is not None and value['focus'] not in FOCUSES:
        raise ValueError('未知优化方向。')
    if not isinstance(value['disabled_lines'], list) or any(x not in data['lines'] for x in value['disabled_lines']):
        raise ValueError('停用产线编号无效。')
    if len(set(value['disabled_lines'])) == len(data['lines']):
        raise ValueError('不能停用所有产线。')
    for name in ('quantities', 'placements'):
        if not isinstance(value[name], dict) or any(pid not in data['demands'] for pid in value[name]):
            raise ValueError('约束包含未知物料。')
    for pid, quantity in value['quantities'].items():
        if type(quantity) not in (int, float) or not math.isfinite(quantity) or not any(abs(quantity-q)<1e-6 for q in data['demands'][pid]['options']):
            options = data['demands'][pid]['options']
            near = sorted(options, key=lambda q: abs(q-quantity))[:5] if type(quantity) in (int, float) and math.isfinite(quantity) else options[:5]
            raise ValueError(f'{pid} 新增量不满足需求、库存上限或整批约束。可选值示例：{sorted(near)} 吨。')
    for pid, placement in value['placements'].items():
        if not isinstance(placement, dict) or set(placement)-{'line_id','start_minute','earliest','latest'}:
            raise ValueError('产线/时间约束格式不正确。')
        lid = placement.get('line_id')
        if lid is not None and (lid not in data['capacities'][pid] or lid in value['disabled_lines']):
            raise ValueError(f'{pid} 不可在指定产线上生产，或该线已停用。')
        for key in ('start_minute', 'earliest', 'latest'):
            n = placement.get(key)
            if n is not None and (type(n) is not int or not 0 <= n <= 1440):
                raise ValueError('时间须为排产日起点起算的0～1440整数分钟。')
        if placement.get('earliest',0) >= placement.get('latest',1440):
            raise ValueError('最早开始必须早于最晚完成。')
    return value


def constrained_data(data, constraints):
    result = copy.deepcopy(data)
    result['user_constraints'] = checked_constraints(data, constraints)
    for pid, quantity in result['user_constraints']['quantities'].items():
        result['demands'][pid]['options'] = [quantity]
    return result


def validate_revision(data, plan, constraints):
    constraints = checked_constraints(data, constraints)
    errors = validate_plan(constrained_data(data, constraints), plan)
    if errors:
        raise ValueError('; '.join(errors))
    for pid, p in plan.items():
        if p['quantity'] + data['demands'][pid]['wip'] <= 0:
            if pid in constraints['placements']:
                raise ValueError(f'{pid} 产量为0，不能指定排程位置。')
            continue
        placement = constraints['placements'].get(pid, {})
        feasible = [lid for lid in data['capacities'][pid]
                    if lid not in constraints['disabled_lines'] and
                    (not placement.get('line_id') or placement['line_id']==lid) and
                    batch_ok(p['quantity'], data['lines'][lid]['batch'])]
        if not feasible:
            raise ValueError(f'{pid} 在当前产线与批量约束下没有可用产线。')
    return constraints
