"""End-to-end smoke check against the running local service (uses configured Agent)."""
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

BASE='http://127.0.0.1:8765'


def request(path, body=None):
    req=Request(BASE+path,data=None if body is None else json.dumps(body,ensure_ascii=False).encode('utf-8'),headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=20) as response:return json.load(response)


if __name__=='__main__':
    history=request('/api/history')
    parent=next(x for x in history if x['eligible'] and x['status']=='completed')
    s=request('/api/sessions',{'job_id':parent['id']});sid=s['id']
    def say(text):return request(f'/api/sessions/{sid}/messages',{'text':text})
    s=say('查询5000611的需求')
    assert s['last_product']=='5000611'
    original=next(p['quantity'] for p in s['products'] if p['id']=='5000611')
    quantity=33 if original!=33 else 35.2
    s=say(f'把它的总产量改成{quantity}吨')
    assert s['constraints']['quantities']['5000611']==quantity
    s=say('5000611安排在二号线，04:00开始')
    assert s['constraints']['placements']['5000611']=={'line_id':'2','start_minute':480}
    s=request(f'/api/sessions/{sid}/messages',{'action':{'type':'config','patch':{'mode':'agent','max_rounds':2,'time_budget':600}}})
    s=say('确认修改，开始优化')
    assert s['active_job_id']
    jid=s['active_job_id']
    print(json.dumps({'session':sid,'parent':parent['id'],'job':jid,'original_quantity':original,'new_quantity':quantity}),flush=True)
    before=time.monotonic();last=None
    while time.monotonic()-before<650:
        s=request('/api/sessions/'+sid)
        phase=s['job']['phase']
        if phase!=last:print(phase,flush=True);last=phase
        if not s['active_job_id']:break
        time.sleep(2)
    j=s['job'];result=next((r for r in j['rounds'] if r['number']==j['best_round']),None)
    assert result and result['evaluation']['eligible'],j.get('stop_reason')
    assert j['parent_job_id']==parent['id']
    assert result['plan']['5000611']['quantity']==quantity
    for r in j['rounds']:
        if 'plan' in r:assert r['plan']['5000611']['quantity']==quantity
    row=next(r for r in result['evaluation']['rows'] if r['product_id']=='5000611')
    assert row['line_id']=='2' and row['start']==480,row
    s=say('为什么5000611安排在二号线')
    assert s['view']['type']=='explain' and '产能' in s['view']['text']
    assert request('/api/jobs/'+parent['id'])['rounds'][0] # original remains readable
    summary={'session':sid,'job':jid,'parent':parent['id'],'best_round':j['best_round'],'rounds':len(j['rounds']),
             'score':result['evaluation']['score'],'eligible':True,'new_quantity':quantity,'line':row['line_id'],'start':row['start'],
             'agent_rounds':sum(r.get('cloud_run_id') is not None for r in j['rounds']),'stop_reason':j['stop_reason']}
    (Path(__file__).parent/'conversation-smoke-result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)
