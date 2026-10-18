"""Isolated Excel adapter for the existing two-stage CP-SAT solver. No DB / write-back."""
import json
import logging
import os
import sys

from data_loader import SOLVER_ROOT, batch_ok, changeover, validate_plan


def solve(data, plan, focus='balanced'):
    errors = validate_plan(data, plan)
    if errors:
        raise ValueError('; '.join(errors))
    sys.path.insert(0, str(SOLVER_ROOT))
    import logging_config
    logging_config._app_logger = logging.getLogger('aps-demo')
    logging_config._app_logger.addHandler(logging.NullHandler())
    import constant
    constant.IS_print_detailed_log = False
    from aps import Aps
    from scheduler import capacity

    def product(pid, rid, quantity, priority=1):
        m = data['materials'][pid]
        return dict(recordId=rid, productId=pid, productName=m['name'],
                    semiFinishedProductId=m['semi'], semiFinishedProductName=m['semi_name'],
                    thirdMaterialGroupId=m['group3'], thirdMaterialGroupDesc=m['group3'],
                    forthMaterialGroupId=m['group4'], forthMaterialGroupDesc=m['group4'],
                    dia=m['diameter'] or 0, productionParticleSize=m['diameter'] or 0,
                    aspectRatio=0, packageType='01' if m['package'] == '包料' else '02',
                    proteinGradeLevel=0, plannedProductionQuantity=quantity, materialPriority=str(priority),
                    isDosing=int(m['dosing']), isColoring=int(m['coloring']),
                    earliestStartTime='0', latestCompletionTime='24', unitElectricityConsumption=0)

    plans = [product(pid, pid, round(p['quantity'] + data['demands'][pid]['wip'], 4), p['priority'])
             for pid, p in plan.items() if p['quantity'] + data['demands'][pid]['wip'] > 0]
    constraints = data.get('user_constraints', {})
    for p in plans:
        pid = p['productId']
        p['demoPlacement'] = constraints.get('placements', {}).get(pid, {})
        p['demoRequired'] = pid in constraints.get('quantities', {}) or bool(p['demoPlacement'])
    if not plans:
        return dict(scheduled=[], unscheduled=[], schedule_details={}, demo_engine='OR-Tools CP-SAT (empty plan)')
    lines = []
    for lid, l in data['lines'].items():
        line = product(l['previous_product'], 'previous-' + lid, 0)
        line.update(productionLineId=lid, productionLineName=l['name'], productionLineDescription=l['workshop'],
                    lineRemainingTime=24, allowOverTime='0', preOrderProduceTime=0,
                    assignedMixers=[], mixerWaitTime=0, electricityPrice=data['prices'])
        lines.append(line)
    nodes = {p['recordId']: data['materials'][p['productId']] for p in plans + lines}
    switch = {r1: {r2: changeover(m1, m2) for r2, m2 in nodes.items()} for r1, m1 in nodes.items()}

    def excel_capacity(ctx):
        for pid, caps in data['capacities'].items():
            for lid, cap in caps.items():
                placement = constraints.get('placements', {}).get(pid, {})
                if lid in constraints.get('disabled_lines', []) or (placement.get('line_id') and placement['line_id'] != lid):
                    continue
                # Only the NEW quantity must be an integral mixer batch. WIP is fixed/exempt.
                if pid in plan and not batch_ok(plan[pid]['quantity'], data['lines'][lid]['batch']):
                    continue
                ctx.product_product_line_capacity_matrix[pid + '-' + lid] = dict(
                    capacity=cap['rate'], min_quantity=0, priority=cap['priority'])
        return True

    capacity.get_product_capacity_list = excel_capacity
    os.environ['CP_SAT_MAX_TIME'] = '8'
    os.environ['CP_SAT_WORKERS'] = '1'
    engine = Aps(plans, lines, switch, 'demo', 'local', 'excel', [])
    weights = dict(material_priority=200, line_priority=20, time_window=400, protein_grade=0,
                   particle_size=80, changeover_time=100, production_balance=200, electricity_cost=1)
    changes = {'setup': {'changeover_time': 400}, 'particle': {'particle_size': 400},
               'balanced': {}, 'valley': {'production_balance': 80, 'changeover_time': 40},
               'delivery': {'material_priority': 600, 'time_window': 800}}
    if focus not in changes:
        raise ValueError('未知排程侧重点')
    weights.update(changes[focus])
    engine.set_constraint_weights(weights)
    result = engine.production_scheduling()
    if not result:
        raise RuntimeError('求解器未返回可用解。')
    result['demo_engine'] = 'OR-Tools CP-SAT / Stage1分配 + Stage2排序'
    result['demo_weights'] = weights
    return result


if __name__ == '__main__':
    try:
        payload = json.load(sys.stdin)
        result = solve(payload['data'], payload['plan'], payload.get('focus', 'balanced'))
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False, default=lambda x: sorted(x) if isinstance(x, set) else str(x)))
    except Exception as exc:
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        sys.exit(1)
