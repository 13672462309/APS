"""Read the supplied workbooks; freeze one business snapshot for a whole loop."""
import hashlib
import json
import math
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
SOLVER_ROOT = ROOT / "aps-feed" / "aps-feed"
DEFAULT_COVER = {"常规1": 2, "常规2": 3, "非常规1": 5, "非常规2": 1}


def ident(value):
    return str(int(value)) if isinstance(value, (int, float)) else str(value).strip()


def num(value):
    return float(value or 0)


def batch_ok(quantity, batch):
    return abs(quantity / batch - round(quantity / batch)) < 1e-6


def load_dataset(coverage=None):
    coverage = coverage or DEFAULT_COVER
    path = ROOT / "APS基础数据0814.xlsx"
    wb = openpyxl.load_workbook(path, data_only=True)
    tables = {s.title: list(s.values) for s in wb}
    wb.close()
    materials = {}
    for r in tables['1-物料数据'][1:]:
        if r[0] is None:
            continue
        pid = ident(r[0])
        materials[pid] = dict(id=pid, name=r[1], semi=ident(r[2]), semi_name=r[3],
                              package=r[4], cycle=num(r[5]), diameter=None if r[6] is None else num(r[6]),
                              process=r[7], group2=r[8], group3=r[9], group4=r[10],
                              dosing=r[11] == '是', coloring=r[12] == '是')
    lines = {ident(r[0]): dict(id=ident(r[0]), name=r[1], workshop=r[2], batch=num(r[3]),
                             extra_hours=num(r[5]), available_minutes=1440)
             for r in tables['5-产线数据'][1:] if r[0] is not None}
    names = {v['name']: k for k, v in lines.items()}
    warnings = []
    for r in tables['4-上次末生产物料'][1:]:
        if r[1] not in names:
            continue
        line = lines[names[r[1]]]
        line['previous_product'] = ident(r[2])
        if line['workshop'] != r[0]:
            warnings.append(f"{line['name']}车间归属冲突，采用 5-产线数据：{line['workshop']}。")
    wip, wip_orders = {}, {}
    for r in tables['3-前置订单数据'][1:]:
        if r[0] is not None:
            pid = ident(r[3])
            wip[pid] = round(wip.get(pid, 0) + num(r[6]), 4)
            wip_orders.setdefault(pid, []).append(dict(order_id=ident(r[0]), workshop=r[1], line=r[2],
                planned=num(r[4]), completed=num(r[5]), remaining=num(r[6]), previous=r[7]=='是'))
    capacities = {}
    for r in tables['6-产能数据'][1:]:
        if r[0] is not None and ident(r[1]) in lines:
            capacities.setdefault(ident(r[0]), {})[ident(r[1])] = dict(rate=num(r[2]), priority=int(r[3] or 0))
    demands, excluded = {}, []
    for r in tables['2-供需数据'][1:]:
        if r[0] is None:
            continue
        pid, category = ident(r[0]), str(r[2])
        days = [num(x) for x in r[3:11]]
        if pid not in materials:
            excluded.append(dict(id=pid, name=r[1], reason='缺少物料主数据', demand=sum(days), safety=num(r[13])))
            continue
        if pid not in capacities:
            excluded.append(dict(id=pid, name=r[1], reason='缺少物料产能', demand=sum(days), safety=num(r[13])))
            continue
        supply, maximum, safety = map(num, r[11:14])
        cover = sum(days[:coverage[category]])
        minimum = round(max(0, cover - supply), 4)
        demand_only = category == '非常规2' or (maximum == 0 and safety == 0)
        target = max(minimum, cover + safety - supply) if not demand_only else minimum
        upper = max(minimum, cover + maximum - supply) if not demand_only else minimum
        # 按单类只允许不可避免的整批尾差，绝不额外备库；其他类严格库存上限。
        options = {0.0} if minimum <= 1e-6 else set()
        for lid in capacities[pid]:
            batch = lines[lid]['batch']
            first = max(1, math.ceil((minimum - 1e-8) / batch))
            last = math.ceil((upper - 1e-8) / batch) if demand_only else math.floor((upper + 1e-8) / batch)
            for n in range(first, max(first - 1, last) + 1):
                options.add(round(n * batch, 4))
        options = sorted(options)
        if not options:
            raise ValueError(f'{pid} 的必需数量、库存上限和整批规则无共同可行值，不能自动放宽。')
        chosen = min((q for q in options if q >= target - 1e-6), default=options[-1])
        demands[pid] = dict(id=pid, name=r[1], category=category, daily=days, supply=supply,
                            avg_sales_7=None if r[14] is None else num(r[14]),
                            avg_sales_30=None if r[15] is None else num(r[15]),
                            stock=max(0, supply-wip.get(pid, 0)), wip=wip.get(pid, 0), safety=safety,
                            maximum=maximum, cover_days=coverage[category], cover_demand=cover,
                            minimum=minimum, target=round(max(0, target), 4), options=options,
                            default_quantity=chosen, priority={'非常规2': 1, '非常规1': 1, '常规2': 2, '常规1': 3}[category])
    for pid in wip:
        if pid not in demands:
            raise ValueError(f'前置订单 {pid} 无法关联供需及产能，不可忽略。')
    warnings.extend(f"排除 {x['id']} {x['name']}：{x['reason']}（覆盖外需求 {x['demand']:g} 吨，安全库存 {x['safety']:g} 吨）。" for x in excluded)
    if any(x['demand'] > 0 for x in excluded):
        raise ValueError('排除项存在实际需求，必须补齐数据才能运行。')
    price_path = SOLVER_ROOT / 'data' / 'v2' / '1130-3.txt'
    price_data = json.loads(price_path.read_text(encoding='utf-8-sig'))
    # The historical sample supplies only a demonstrator tariff, not demand or capacity.
    def find_prices(obj):
        if isinstance(obj, dict):
            if obj.get('electricityPrice'):
                return obj['electricityPrice']
            for v in obj.values():
                found = find_prices(v)
                if found:
                    return found
        elif isinstance(obj, list):
            for v in obj:
                found = find_prices(v)
                if found:
                    return found
        return None
    prices = find_prices(price_data)
    if not prices:
        raise ValueError('历史样例未找到电价时段。')
    shifts = [list(r[:4]) for r in tables['车间班次'][1:] if r[0]]
    # Only the supplied continuous 20–08 / 08–20 calendar is supported in this prototype.
    for workshop in {l['workshop'] for l in lines.values()}:
        spans = {(str(r[2]).replace('：', ':').replace(' ', ''), str(r[3]).replace('：', ':').replace(' ', '')) for r in shifts if r[0] == workshop}
        if spans != {('08:00', '20:00'), ('20:00', '08:00')}:
            raise ValueError(f'{workshop} 班次已变化，需要更新日历适配器。')
    sources = [path, ROOT / 'APS_排产规则汇总表.xlsx', price_path]
    fingerprints = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    return dict(materials=materials, demands=demands, capacities=capacities, wip_orders=wip_orders, presentation_version=1,
                lines=dict(sorted(lines.items())), prices=prices, shifts=shifts, excluded=excluded,
                warnings=warnings, fingerprints=fingerprints, coverage=coverage,
                assumptions=[
                    'T 为用户选择的生产日：前一日20:00至当日20:00；原表无日期，T～T+7按相对需求解释。',
                    '按两班连续24小时排程，不启用额外2小时；未提供独立休息/检修窗口，暂不扣除。',
                    '供应已含在制；前置订单余量锁定，与同SKU新增量合并为一个不拆线任务，仅新增量约束整批。',
                    '非常规2及库存上下限同时为0的物料只补缺口，允许最小整批尾差；其他物料遵守覆盖期末库存上限。',
                    '准备条件暂按已满足，交期统一生产日20:00；并非已验证原料齐套或真实订单级交期。',
                    '谷电时段借用 aps-feed/data/v2/1130-3.txt 历史样例，待工厂确认；未计算真实电费。',
                    '粒径为空不参与相邻粒径统计；未配置共享混合机和蛋白等级，相关约束未启用。'])


def default_plan(data):
    return {pid: dict(quantity=d['default_quantity'], priority=d['priority']) for pid, d in data['demands'].items()}


def validate_plan(data, plan):
    errors = []
    if set(plan) != set(data['demands']):
        return ['物料集合被改变，禁止增删基础需求。']
    for pid, item in plan.items():
        q = item.get('quantity')
        if isinstance(q, bool) or not isinstance(q, (float, int)) or not math.isfinite(q):
            errors.append(f'{pid} 数量必须为有限数值。')
        elif not any(abs(q - v) < 1e-6 for v in data['demands'][pid]['options']):
            errors.append(f'{pid} 数量 {q} 不在合法整批选项内。')
        if type(item.get('priority')) is not int or item['priority'] not in (1, 2, 3):
            errors.append(f'{pid} 优先级须为1、2或3。')
    return errors


def changeover(a, b):
    if a['id'] == b['id']:
        return 0
    durations = [30 if a['diameter'] != b['diameter'] else 0,
                 20 if a['semi'] != b['semi'] else 0, 60 if a['group2'] != b['group2'] else 0]
    if '禽' in str(b['group2']):
        durations.append(45 if a['group4'] != b['group4'] else 0)
    else:
        durations.append(60 if a['group3'] != b['group3'] else 0)
    return max(durations) + (20 if a['dosing'] or a['coloring'] or b['dosing'] or b['coloring'] else 0)
