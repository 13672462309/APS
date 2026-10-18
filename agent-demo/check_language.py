"""Optional real AgentCore intent-routing smoke check, with no scheduling run."""
import json
import urllib.request


def post(path, payload):
    request = urllib.request.Request('http://127.0.0.1:8765'+path,
        data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=180) as response:
        return json.load(response)


if __name__=='__main__':
    session=post('/api/sessions',{})
    print('检验自然语言解析，无求解任务。',flush=True)
    result=post('/api/sessions/'+session['id']+'/messages',
                {'text':'请把这一轮最多尝试的次数设为三次'})
    assert result['config']['max_rounds']==3,result['messages'][-1]['text']
    assert not result['versions']
    print(json.dumps({'session':session['id'],'max_rounds':result['config']['max_rounds'],
                      'reply':result['messages'][-1]['text']},ensure_ascii=False))
