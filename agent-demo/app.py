"""Local prototype API. Explicit routes only; never expose project files or .env."""
import csv
import io
import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, request, send_file, jsonify

from agentcore_client import load_config, agent_url, AgentCoreError
from controller import Manager, DEFAULT_CONFIG
from data_loader import ROOT, load_dataset, default_plan
from evaluation import METRIC_NAMES
from demand_fields import catalog

BASE = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=str(BASE / 'static'))
app.json.ensure_ascii = False
app.json.sort_keys = False
app.config['MAX_CONTENT_LENGTH'] = 128 * 1024
manager = Manager()
from conversation import Conversations
conversations = Conversations(manager)


@app.post('/api/sessions')
def create_session():
    payload = request.get_json()
    if not isinstance(payload, dict):
        return {'error': '请求必须为对象'}, 400
    return conversations.create(payload.get('config'), payload.get('job_id')), 201


@app.get('/api/sessions/<sid>')
def get_session(sid):
    return conversations.get(sid)


@app.get('/api/sessions')
def session_history():
    return jsonify(conversations.history())


@app.post('/api/sessions/<sid>/messages')
def chat(sid):
    payload = request.get_json()
    if not isinstance(payload, dict):
        return {'error': '请求必须为对象'}, 400
    return conversations.message(sid, payload.get('text', ''), payload.get('action'))


@app.before_request
def local_requests_only():
    # Local-only UI/API, no permissive CORS or cross-site state changes.
    if request.host.split(':')[0] not in ('127.0.0.1', 'localhost', '[::1]'):
        return {'error': '仅允许本机访问'}, 403
    if request.method == 'POST':
        origin = request.headers.get('Origin')
        if origin and origin != request.host_url.rstrip('/'):
            return {'error': '拒绝跨来源请求'}, 403
        if not request.is_json:
            return {'error': '请求须为application/json'}, 415


@app.after_request
def response_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.get('/')
def index():
    return send_file(ROOT / '排产智能体平台-前端.html')


@app.get('/api/health')
def health():
    return dict(app='aps-feed-demo', status='ok')


@app.get('/api/bootstrap')
def bootstrap():
    data = load_dataset()
    configured = False
    try:
        endpoint, _ = load_config(BASE / '.env')
        agent_url(endpoint)
        configured = True
    except AgentCoreError:
        pass
    return dict(config=DEFAULT_CONFIG, agent_configured=configured, metrics=METRIC_NAMES, demand_fields=catalog(),
                dataset=dict(material_count=len(data['materials']), demand_count=len(data['demands']),
                             capacity_count=sum(map(len, data['capacities'].values())), lines=list(data['lines'].values()),
                             warnings=data['warnings'], assumptions=data['assumptions'], shifts=data['shifts'],
                             fingerprints=data['fingerprints']), history=manager.history())


@app.get('/api/dataset')
def dataset():
    data = load_dataset()
    return dict(demands=list(data['demands'].values()), excluded=data['excluded'],
                warnings=data['warnings'], assumptions=data['assumptions'])


@app.post('/api/jobs')
def create_job():
    payload = request.get_json()
    if not isinstance(payload, dict):
        return {'error': '请求格式错误'}, 400
    goal = payload.get('goal', '满足交付与整批约束，综合优化六项指标。')
    if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 2000:
        return {'error': '排产目标不能为空或超过2000字。'}, 400
    try:
        return manager.start(payload.get('config', {}), goal.strip()), 202
    except (ValueError, TypeError) as exc:
        return {'error': str(exc)}, 400


@app.get('/api/jobs/<job_id>')
def get_job(job_id):
    return conversations.present_job(job_id)


@app.post('/api/jobs/<job_id>/cancel')
def cancel_job(job_id):
    manager.get(job_id)
    job = manager.jobs.get(job_id)
    if job and job.snapshot()['status'] in ('running', 'queued'):
        job.cancel.set()
    return dict(ok=True)


@app.get('/api/history')
def history():
    return jsonify(manager.history())


def best_result(job):
    return next((r for r in job['rounds'] if r['number'] == job['best_round']), None)


@app.get('/api/jobs/<job_id>/export/<kind>')
def export(job_id, kind):
    job = manager.get(job_id)
    best = best_result(job)
    if not best:
        return {'error': '尚无已求解结果可导出'}, 409
    if kind == 'json':
        content = json.dumps(job, ensure_ascii=False, indent=2).encode('utf-8')
        return send_file(io.BytesIO(content), mimetype='application/json', as_attachment=True,
                         download_name=f'APS-{job_id[:8]}.json')
    if kind != 'csv':
        return {'error': '仅支持json或csv'}, 404
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(['状态', '轮次', '物料编号', '物料名称', '产线', '新增吨数', '在制吨数', '总吨数', '开始时间', '结束时间', '换型分钟'])
    start = datetime.fromisoformat(job['config']['schedule_date']) - timedelta(hours=4)
    def safe(value):
        text = str(value)
        return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
    for row in best['evaluation']['rows']:
        writer.writerow([safe(v) for v in [
            'Demo可行（需确认口径）' if best['evaluation']['eligible'] else '不可执行候选', best['number'],
            row['product_id'], row['name'], row['line_name'], row['new_quantity'], row['wip'], row['quantity'],
            (start + timedelta(minutes=row['start'])).isoformat(' ', timespec='minutes'),
            (start + timedelta(minutes=row['end'])).isoformat(' ', timespec='minutes'), row['switch']]])
    return send_file(io.BytesIO(out.getvalue().encode('utf-8-sig')), mimetype='text/csv', as_attachment=True,
                     download_name=f'APS-{job_id[:8]}.csv')


@app.errorhandler(KeyError)
def missing(exc):
    return {'error': '任务不存在'}, 404


@app.errorhandler(ValueError)
def invalid(exc):
    return {'error': str(exc)}, 400


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.environ.get('APS_DEMO_PORT', '8765')), debug=False, threaded=True)
