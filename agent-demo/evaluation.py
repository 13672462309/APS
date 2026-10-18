"""Independent validation and fixed KPI denominators; never trust an LLM's scores."""
import math
import statistics
from data_loader import batch_ok, changeover, validate_plan

METRIC_NAMES = {'feasibility': '排产可行性', 'particle': '粒径从小到大', 'delivery': '及时交付',
                'oee': '理论OEE', 'balance': '产线均衡度', 'valley': '谷电利用率'}
SOFT_WEIGHTS = {'particle': .2, 'delivery': .3, 'oee': .2, 'balance': .2, 'valley': .1}


def ratio(n, d, note):
    return dict(value=round(n / d * 100, 2) if d else None, numerator=round(n, 4), denominator=round(d, 4), note=note)


def evaluate(data, plan, raw):
    errors = validate_plan(data, plan)
    planned = {pid for pid, p in plan.items() if p['quantity'] + data['demands'][pid]['wip'] > 0}
    invalid, seen, rows, line_rows, violations = set(), set(), [], [], list(errors)
    particle_good = particle_all = on_time = production = operation = valley_used = valley_total = 0
    min_price = min(float(p['ePrice']) for p in data['prices'])
    valley = [(float(p['electricityStartTime']) * 60, float(p['electricityEndTime']) * 60)
              for p in data['prices'] if float(p['ePrice']) == min_price]
    for lid, line in data['lines'].items():
        tasks = raw.get('schedule_details', {}).get(lid, {}).get('scheduled_products', [])
        tasks = sorted(tasks, key=lambda t: t['start_time'])
        previous, end, prod, setup, max_end = data['materials'][line['previous_product']], 0, 0, 0, 0
        previous_scheduled = None
        for t in tasks:
            pid = str(t['rid'])
            if pid not in planned:
                violations.append(f'{lid} 出现未知或不应排产任务 {pid}')
                errors.append('未知任务')
                continue
            if pid in seen:
                invalid.add(pid)
                violations.append(f'{pid} 重复排产或被拆线')
            seen.add(pid)
            m, d, p = data['materials'][pid], data['demands'][pid], plan[pid]
            q = p['quantity'] + d['wip']
            start, finish = float(t['start_time']), float(t['end_time'])
            cap = data['capacities'].get(pid, {}).get(lid)
            need = math.ceil(q * 60 / cap['rate']) if cap and cap['rate'] else None
            sw = changeover(previous, m)
            reasons = []
            constraints = data.get('user_constraints', {})
            placement = constraints.get('placements', {}).get(pid, {})
            if lid in constraints.get('disabled_lines', []) or (placement.get('line_id') and placement['line_id'] != lid):
                reasons.append('不满足用户产线约束')
            if 'start_minute' in placement and abs(start-placement['start_minute']) > .01:
                reasons.append('不满足用户固定开始时间')
            if start < placement.get('earliest', 0) or finish > placement.get('latest', 1440):
                reasons.append('不满足用户时间窗口')
            if not cap or not batch_ok(p['quantity'], line['batch']):
                reasons.append('产能或整批不符')
            if need is None or abs(finish - start - need) > .01:
                reasons.append('生产时长不符')
            if start < end + sw - .01:
                reasons.append('重叠或换型时间不足')
            if start < 0 or finish > line['available_minutes'] or finish <= start:
                reasons.append('超出开班窗口')
            if reasons:
                invalid.add(pid)
                violations.append(f"{pid} / {line['name']}：{'、'.join(reasons)}")
            if finish <= 1440 and not reasons:
                on_time += 1
            if previous_scheduled is not None and previous_scheduled['diameter'] is not None and m['diameter'] is not None:
                particle_all += 1
                particle_good += previous_scheduled['diameter'] <= m['diameter']
            valley_used += sum(max(0, min(finish, b) - max(start, a)) for a, b in valley)
            prod += finish - start
            setup += sw
            max_end = max(max_end, finish)
            rows.append(dict(product_id=pid, name=m['name'], line_id=lid, line_name=line['name'],
                             quantity=round(q, 4), new_quantity=p['quantity'], wip=d['wip'],
                             start=start, end=finish, duration=finish-start, switch=sw, switch_start=end,
                             diameter=m['diameter'], priority=p['priority'], valid=not reasons))
            previous, previous_scheduled, end = m, m, finish
        # Operating span starts with first setup at t=0 in the source solver's result.
        # Includes intentional idle before/in between tasks, excludes shutdown after last task.
        operation += max_end
        production += prod
        valley_total += sum(max(0, min(b, line['available_minutes']) - max(a, 0)) for a, b in valley)
        line_rows.append(dict(id=lid, name=line['name'], workshop=line['workshop'], production_minutes=prod,
                              switch_minutes=setup, operation_minutes=max_end, available_minutes=line['available_minutes'],
                              utilization=round(prod / line['available_minutes'] * 100, 2), count=len(tasks)))
    missing = planned - seen
    invalid |= missing
    violations.extend(f'{pid} / {data["materials"][pid]["name"]} 未排入产线' for pid in sorted(missing))
    # Demand obligations are immutable across iterations, including locked WIP completion.
    required = {pid for pid, d in data['demands'].items() if d['minimum'] > 0 or d['wip'] > 0}
    covered = {r['product_id'] for r in rows if r['valid'] and r['end'] <= 1440 and
               r['new_quantity'] >= data['demands'][r['product_id']]['minimum'] - 1e-6}
    hard_coverage = ratio(len(required & covered), len(required), '固定覆盖期缺口SKU及在制任务；不允许通过少报计划提高分数。')
    loads = [l['production_minutes'] / l['available_minutes'] for l in line_rows]
    mean = statistics.mean(loads)
    cv = statistics.pstdev(loads) / mean if mean else None
    metrics = {
        'feasibility': ratio(len(planned - invalid), len(planned), '逐计划独立核验产线、整批、开班、换型、时长；准备条件暂按已满足。'),
        'particle': ratio(particle_good, particle_all, '同产线相邻已排任务；任一粒径为空的相邻对不计入分母。'),
        'delivery': ratio(on_time, len(planned), '计划于生产日20:00前完成；当前无订单级交期，采用统一交期。'),
        'oee': ratio(production, operation, '有效生产分钟 / 各已启用产线从t=0至末任务结束的开机分钟；含换型及等待。'),
        'balance': dict(value=round((1-cv)*100, 2) if cv is not None else None,
                        numerator=statistics.pstdev(loads), denominator=mean,
                        note='1−σ/μ，按7条固定产线的生产时长/可用时长计算；空闲线计入，可为负数。'),
        'valley': ratio(valley_used, valley_total, '低价时段生产分钟 / 7条产线的全部低价可用分钟；历史电价样例口径。')}
    for k, v in metrics.items():
        v['name'] = METRIC_NAMES[k]
    eligible = not errors and not invalid and bool(planned) and required <= covered
    active_weights = {k: w for k, w in SOFT_WEIGHTS.items() if metrics[k]['value'] is not None}
    score = sum(max(0, min(100, metrics[k]['value'])) * w for k, w in active_weights.items()) / (sum(active_weights.values()) or 1)
    supply_projection = []
    scheduled_qty = {r['product_id']: r['quantity'] for r in rows if r['valid']}
    for pid, d in data['demands'].items():
        remaining = round(d['stock'] + scheduled_qty.get(pid, 0) - d['cover_demand'], 4)
        if pid in planned or remaining < d['safety']:
            supply_projection.append(dict(product_id=pid, name=d['name'], projected_stock=remaining,
                                          safety=d['safety'], safety_shortfall=round(max(0, d['safety']-remaining), 4)))
    return dict(metrics=metrics, score=round(score, 3), eligible=eligible,
                hard_coverage=hard_coverage, violations=violations, rows=rows, lines=line_rows,
                unscheduled=sorted(missing), total_quantity=round(sum(r['quantity'] for r in rows), 4),
                planned_count=len(planned), scheduled_count=len(seen), score_weights=SOFT_WEIGHTS,
                supply_projection=supply_projection)


def rank(result):
    """Feasibility is lexicographic; no soft score can buy a hard violation."""
    e = result['evaluation']
    return (e['eligible'], e['hard_coverage']['value'] or 0,
            e['metrics']['feasibility']['value'] or 0, e['score'])
