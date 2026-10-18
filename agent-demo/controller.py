"""Deterministic loop ownership: validate -> solve -> evaluate -> retain best -> stop."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from datetime import date, datetime

from data_loader import load_dataset, default_plan, validate_plan, DEFAULT_COVER
from evaluation import evaluate, rank, METRIC_NAMES
from planning_state import constrained_data, validate_revision

BASE = Path(__file__).resolve().parent
RUNS = BASE / 'runs'
DEFAULT_CONFIG = dict(mode='agent', max_rounds=4, time_budget=600, patience=2, min_gain=.2,
                      schedule_date=date.today().isoformat(), coverage=DEFAULT_COVER,
                      thresholds={'feasibility': 100, 'particle': 95, 'delivery': 100,
                                  'oee': 85, 'balance': 80, 'valley': 80})


def checked_config(value):
    if not isinstance(value, dict):
        raise ValueError('配置必须为对象。')
    config = copy.deepcopy(DEFAULT_CONFIG)
    unknown = set(value) - set(config)
    if unknown:
        raise ValueError('未知配置项：' + ','.join(sorted(unknown)))
    config.update(value)
    if config['mode'] not in ('agent', 'baseline'):
        raise ValueError('模式只能为 agent / baseline')
    for key, low, high in [('max_rounds', 1, 10), ('time_budget', 30, 1800), ('patience', 1, 10)]:
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f'{key} 必须是 {low}～{high} 的整数。')
    if type(config['min_gain']) not in (int, float) or not math.isfinite(config['min_gain']) or not 0 <= config['min_gain'] <= 20:
        raise ValueError('min_gain 必须在0～20之间。')
    date.fromisoformat(config['schedule_date'])
    ranges = {'常规1': (1, 2), '常规2': (2, 5), '非常规1': (3, 7), '非常规2': (1, 1)}
    if not isinstance(config['coverage'], dict) or set(config['coverage']) != set(ranges):
        raise ValueError('覆盖期分类不完整。')
    for k, (a, b) in ranges.items():
        if type(config['coverage'][k]) is not int or not a <= config['coverage'][k] <= b:
            raise ValueError(f'{k} 覆盖期须在{a}～{b}天。')
    if not isinstance(config['thresholds'], dict) or set(config['thresholds']) != set(METRIC_NAMES):
        raise ValueError('六项指标阈值必须完整。')
    for k, v in config['thresholds'].items():
        if type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 100:
            raise ValueError('指标阈值必须为0～100的数值。')
    if config['thresholds']['feasibility'] != 100 or config['thresholds']['delivery'] != 100:
        raise ValueError('原型中可行性和交付门槛固定100%，不能降低硬门槛。')
    return config


def apply_proposal(data, base_plan, proposal):
    if not isinstance(proposal, dict) or set(proposal) - {'adjustments', 'focus', 'reason', 'expected_effect'}:
        raise ValueError('Agent 输出字段不符合排产参数契约。')
    changes = proposal.get('adjustments')
    if not isinstance(changes, list) or len(changes) > len(base_plan):
        raise ValueError('adjustments 必须是合法数组。')
    if proposal.get('focus') not in ('balanced', 'setup', 'particle', 'valley', 'delivery'):
        raise ValueError('focus 不符合约定。')
    for key in ('reason', 'expected_effect'):
        if not isinstance(proposal.get(key), str) or len(proposal[key]) > 3000:
            raise ValueError(f'{key} 缺失或过长。')
    plan, seen = copy.deepcopy(base_plan), set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != {'product_id', 'quantity', 'priority'}:
            raise ValueError('每个调整须且仅含product_id、quantity、priority。')
        pid = change['product_id']
        if not isinstance(pid, str) or pid not in plan or pid in seen:
            raise ValueError(f'未知或重复物料 {pid}')
        seen.add(pid)
        plan[pid] = dict(quantity=change['quantity'], priority=change['priority'])
    errors = validate_plan(data, plan)
    if errors:
        raise ValueError('; '.join(errors))
    return plan


def thresholds_met(evaluation, config):
    return evaluation['eligible'] and all(v['value'] is None or v['value'] >= config['thresholds'][k]
                                          for k, v in evaluation['metrics'].items())


def context_for(data, base_plan, previous, config, goal, attempts):
    candidates = []
    # Each proposal is a bounded neighbourhood move. All original requirements remain
    # frozen in the validator; successive iterations rotate through actionable SKUs.
    actionable = [(pid, d) for pid, d in data['demands'].items() if max(d['options']) > 0 or d['wip'] > 0]
    actionable.sort(key=lambda item: (item[1]['minimum'] > 0, base_plan[item[0]]['quantity']), reverse=True)
    offset = len(attempts) * 8 % max(len(actionable), 1)
    chosen = (actionable[offset:] + actionable[:offset])[:8]
    for pid, d in chosen:
        if max(d['options']) == 0 and d['wip'] == 0:
            continue
        m = data['materials'][pid]
        choices = d['options']
        # Bounded legal menu keeps cloud context compact while retaining useful local moves.
        anchors = [0, d['minimum'], d['target'], base_plan[pid]['quantity'],
                   base_plan[pid]['quantity']-4.4, base_plan[pid]['quantity']+4.4,
                   choices[-1]/2, choices[-1]]
        menu = sorted({min(choices, key=lambda q: abs(q-a)) for a in anchors})
        candidates.append({k: d[k] for k in ('id', 'category', 'supply', 'wip', 'minimum', 'target')} |
                          dict(options=menu, semi=m['semi'], diameter=m['diameter'], capacities=data['capacities'][pid]))
    feedback = None
    if previous:
        e = previous['evaluation']
        feedback = {k: e[k] for k in ('score', 'eligible', 'violations', 'lines', 'unscheduled')}
        feedback['metrics'] = {k: v['value'] for k, v in e['metrics'].items()}
        feedback['evaluated_adjustments'] = previous['proposal']['adjustments']
        feedback['evaluated_round'] = previous['number']
        feedback['base_note'] = '反馈来自上一轮；base_plan是历史最佳的计划，二者可能不同。'
        feedback['safety_shortfalls'] = [x for x in e.get('supply_projection', []) if x['safety_shortfall'] > 0]
    return dict(task='只选择本轮候选物料的数量和优先级，直接输出一个短JSON，不调用工具', goal=goal,
                snapshot=dict(materials=candidates, lines=[{k:l[k] for k in ('id','batch')} for l in data['lines'].values()],
                              all_plan_summary=dict(tasks=sum(v['quantity']+data['demands'][pid]['wip']>0 for pid,v in base_plan.items()),
                                                    new_tons=round(sum(v['quantity'] for v in base_plan.values()), 2)),
                              neighbourhood_note='本轮仅优化候选清单；其他物料保持原计划。候选按轮次轮换，完整需求由后端校验。'),
                base_plan={d['id']: base_plan[d['id']] for d in candidates}, feedback=feedback,
                targets=config['thresholds'], attempts=attempts[-3:], user_constraints=data.get('user_constraints', {}),
                instruction='调整相对base_plan，使用options里的精确值；后端会自动叠加wip。不要返回时刻或指标。')


class Stopped(Exception):
    pass


class Job:
    def __init__(self, config, goal, revision=None):
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.value = dict(id=uuid.uuid4().hex, status='queued', phase='等待启动', config=config,
                          goal=goal, created_at=datetime.now().isoformat(timespec='seconds'),
                          events=[], rounds=[], best_round=None, stop_reason=None, elapsed=0)
        self.folder = RUNS / self.value['id']
        self.folder.mkdir(parents=True)
        self.started = time.monotonic()
        self.deadline = self.started + config['time_budget']
        self.revision = copy.deepcopy(revision)
        if revision:
            self.value.update(parent_job_id=revision.get('parent_job_id'), session_id=revision.get('session_id'),
                              constraints=revision.get('constraints', {}), seed_plan=revision['plan'])

    def snapshot(self):
        with self.lock:
            value = copy.deepcopy(self.value)
            if value['status'] in ('queued', 'running'):
                value['elapsed'] = round(time.monotonic()-self.started, 1)
            return value

    def update(self, **values):
        with self.lock:
            self.value.update(values)
            self.value['elapsed'] = round(time.monotonic() - self.started, 1)
            temp = self.folder / 'job.tmp'
            temp.write_text(json.dumps(self.value, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(self.folder / 'job.json')

    def event(self, phase, text):
        with self.lock:
            self.value['events'].append(dict(time=datetime.now().strftime('%H:%M:%S'), phase=phase, text=text))
            self.update(phase=phase)

    def checkpoint(self):
        if self.cancel.is_set():
            raise Stopped('用户停止：已终止本地计算并保留历史最佳。云端取消状态见运行日志。')
        if time.monotonic() >= self.deadline:
            raise Stopped('达到总时间预算：保留历史最佳结果。')

    def worker(self, script, payload, timeout):
        self.checkpoint()
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        process = subprocess.Popen([sys.executable, str(BASE / script)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                   cwd=str(BASE), creationflags=flags)
        deadline = min(self.deadline, time.monotonic() + timeout)
        initial = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        progress_stage = None
        succeeded = False
        try:
            while True:
                self.checkpoint()
                if payload.get('progress_path'):
                    try:
                        stage = json.loads(Path(payload['progress_path']).read_text(encoding='utf-8'))['stage']
                        if stage != progress_stage:
                            progress_stage = stage
                            self.event('Agent排产', stage + '，等待完整成功响应后再校验方案。')
                    except (OSError, ValueError, KeyError):
                        pass
                if time.monotonic() >= deadline:
                    raise Stopped(f'{"Agent调用" if script.startswith("agent") else "求解"} 超时，保留已完成结果。')
                try:
                    out, err = process.communicate(input=initial, timeout=.4)
                    break
                except subprocess.TimeoutExpired:
                    initial = None
            try:
                result = json.loads(out.decode('utf-8'))
            except (ValueError, UnicodeError):
                raise RuntimeError(f'{script} 异常退出（{process.returncode}），未返回有效JSON。') from None
            if process.returncode != 0 or not result.get('ok'):
                raise RuntimeError(result.get('error', '子进程求解失败'))
            succeeded = True
            return result['result']
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
            if not succeeded and script == 'agent_worker.py' and payload.get('run_id'):
                from agentcore_client import load_config, stop_agent
                try:
                    endpoint, key = load_config(BASE / '.env')
                    stopped = stop_agent(endpoint, key, payload['thread_id'], payload['run_id'])
                except Exception:
                    stopped = False
                self.event('取消云端请求', '云端已确认停止。' if stopped else '未收到云端停止确认；不会自动重试，请在AgentCore检查该运行。')

    def run(self):
        try:
            self.update(status='running')
            self.event('读取数据', '冻结本轮运行的Excel快照、需求边界与评分口径。')
            data = copy.deepcopy(self.revision['data']) if self.revision else load_dataset(self.value['config']['coverage'])
            if self.revision:
                constraints = validate_revision(data, self.revision['plan'], self.revision['constraints'])
                data = constrained_data(data, constraints)
            (self.folder / 'snapshot.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            self.update(warnings=data['warnings'], assumptions=data['assumptions'], fingerprints=data['fingerprints'])
            base = copy.deepcopy(self.revision['plan']) if self.revision else default_plan(data)
            best, previous, history, hashes, stale = None, None, [], set(), 0
            config = self.value['config']
            for number in range(1, config['max_rounds'] + 1):
                self.checkpoint()
                begin = time.monotonic()
                source, cloud = '固定规则基准（未调用Agent）', None
                if config['mode'] == 'agent' and not (self.revision and number == 1):
                    source = 'AgentCore真实调用'
                    self.event('Agent排产', f'第{number}轮：根据需求与排程反馈生成数量、优先级及优化侧重点。')
                    run_id = 'run-' + uuid.uuid4().hex
                    self.update(current_agent_run=dict(thread_id='aps-demo-' + self.value['id'], run_id=run_id))
                    cloud = self.worker('agent_worker.py', dict(thread_id='aps-demo-' + self.value['id'], run_id=run_id,
                        progress_path=str(self.folder / f'progress-{number}.json'),
                        context=context_for(data, base, previous, config, self.value['goal'], history)), 240)
                    proposal = cloud['proposal']
                else:
                    source = '用户确认方案基准' if self.revision else source
                    proposal = dict(adjustments=[], focus='balanced', reason='先计算用户确认的方案，再在约束内优化。' if self.revision else '逐日覆盖需求，加安全库存补货，按可行整批取量。', expected_effect='保留可追溯的本轮基准。')
                if data.get('user_constraints', {}).get('focus'):
                    proposal['focus'] = data['user_constraints']['focus']
                try:
                    plan = apply_proposal(data, base, proposal)
                except ValueError as exc:
                    history.append(dict(round=number, error=str(exc)))
                    self.record_round(dict(number=number, status='rejected', source=source, proposal=proposal, error=str(exc)))
                    self.event('方案校验', f'第{number}轮参数被拒绝：{exc}')
                    stale += 1
                    if stale >= config['patience']:
                        self.finish('连续无有效改进，达到耐心上限。')
                        return
                    continue
                digest = hashlib.sha256(json.dumps([plan, proposal['focus']], sort_keys=True).encode()).hexdigest()
                if digest in hashes:
                    self.record_round(dict(number=number, status='duplicate', source=source, proposal=proposal, error='方案与已求解方案完全一致'))
                    self.finish('Agent返回重复方案，停止重复求解。')
                    return
                hashes.add(digest)
                self.event('求解排程', f'第{number}轮参数校验通过，调用现有两阶段CP-SAT求解器。')
                raw = self.worker('solver_worker.py', dict(data=data, plan=plan, focus=proposal['focus']), 65)
                self.event('指标评估', f'第{number}轮求解完成，独立校验约束并计算六项指标。')
                e = evaluate(data, plan, raw)
                result = dict(number=number, status='evaluated', source=source, proposal=proposal, plan=plan,
                              evaluation=e, elapsed=round(time.monotonic()-begin, 2),
                              cloud_run_id=cloud['run_id'] if cloud else None, engine=raw.get('demo_engine'))
                (self.folder / f'solver-{number}.json').write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
                old_best = best
                improved = best is None or rank(result) > rank(best)
                significant = best is None or rank(result)[:3] > rank(best)[:3] or (improved and e['score'] - best['evaluation']['score'] >= config['min_gain'])
                if improved:
                    best = result
                    self.update(best_round=number)
                stale = 0 if significant else stale + 1
                result['accepted_as_best'] = improved
                self.record_round(result)
                history.append(dict(round=number, focus=proposal['focus'], adjustments=proposal['adjustments'], score=e['score'], eligible=e['eligible']))
                previous = result
                base = copy.deepcopy(best['plan'])
                self.event('反馈优化', f"第{number}轮评分{e['score']:.2f}；{'可行' if e['eligible'] else '存在约束问题'}；当前最佳第{best['number']}轮。")
                if config['mode'] == 'baseline':
                    self.finish('规则基准计算完成（仅一轮，不调用Agent）。')
                    return
                if thresholds_met(e, config):
                    self.finish('所有适用指标达到设定阈值。')
                    return
                if stale >= config['patience']:
                    self.finish('连续改进不足，达到耐心上限。')
                    return
            self.finish('达到最大迭代轮数。')
        except Stopped as exc:
            self.finish(str(exc), 'cancelled' if self.cancel.is_set() else 'completed')
        except Exception as exc:
            self.event('运行错误', str(exc)[:1000])
            self.finish('运行失败：' + str(exc)[:1000], 'failed')

    def record_round(self, result):
        with self.lock:
            self.value['rounds'].append(result)
            self.update()

    def finish(self, reason, status='completed'):
        self.event('结束', reason)
        self.update(status=status, stop_reason=reason)


class Manager:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}
        RUNS.mkdir(exist_ok=True)
        for file in RUNS.glob('*/job.json'):
            try:
                value = json.loads(file.read_text(encoding='utf-8'))
                if value['status'] in ('queued', 'running'):
                    value.update(status='interrupted', stop_reason='本地服务重启，任务未自动重试；已完成轮次保留。')
                    file.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
            except (ValueError, KeyError):
                pass

    def start(self, config, goal, revision=None):
        with self.lock:
            if any(j.snapshot()['status'] in ('queued', 'running') for j in self.jobs.values()):
                raise ValueError('已有任务运行中，请先停止或等待完成。')
            job = Job(checked_config(config), goal, revision)
            self.jobs[job.value['id']] = job
            job.update()
            threading.Thread(target=job.run, daemon=True, name='aps-loop').start()
            return job.snapshot()

    def get(self, job_id):
        if len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
            raise KeyError('未知任务')
        if job_id in self.jobs:
            return self.jobs[job_id].snapshot()
        path = RUNS / job_id / 'job.json'
        if not path.exists():
            raise KeyError('未知任务')
        return json.loads(path.read_text(encoding='utf-8'))

    def history(self):
        values = []
        for file in RUNS.glob('*/job.json'):
            try:
                job = self.get(file.parent.name)
                best = next((r for r in job['rounds'] if r['number'] == job['best_round']), None)
                values.append({k: job[k] for k in ('id', 'status', 'created_at', 'goal', 'best_round', 'stop_reason')} |
                              dict(rounds=len(job['rounds']), mode=job['config']['mode'],
                                   score=best['evaluation']['score'] if best else None,
                                   eligible=best['evaluation']['eligible'] if best else False))
            except (ValueError, KeyError):
                continue
        return sorted(values, key=lambda x: x['created_at'], reverse=True)
