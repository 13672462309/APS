"""Persistent, evidence-backed conversation and versioned planning drafts."""
import copy
import json
import re
import threading
import uuid
from datetime import datetime, date, timedelta
from pathlib import Path

from controller import checked_config, DEFAULT_CONFIG, RUNS
from data_loader import load_dataset, default_plan
from planning_state import checked_constraints, validate_revision, FOCUSES
from demand_fields import enrich_snapshot, rows_for
from order_details import order_details_for

BASE = Path(__file__).resolve().parent
SESSIONS = BASE / 'sessions'
ACTIVE = ('queued', 'running')
PHASES = ['数据校验', '供需分析', '目标确认', '求解调优', '结果校验', '方案输出']


def best(job):
    return next((r for r in job['rounds'] if r['number'] == job['best_round']), None)


def minute(text):
    match = re.search(r'(\d{1,2})[:：](\d{2})', text)
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise ValueError('时间请写成 HH:MM，例如08:00。')
    return ((int(match[1])-20) % 24)*60 + int(match[2])


def detect(text, state):
    """Fast deterministic intents; unsupported wording falls through to AgentCore."""
    if re.search(r'如果|假如|会怎样|会怎么样|不要|暂不|先不|不需要|别把', text): return {'type':'help','unrecognized':True}
    if re.search(r'返回完整|返回全部|完整表单|全部物料',text):return {'type':'query','all':True}
    if re.search(r'上一步|返回前一步|退回上一步',text):return {'type':'back'}
    for label,phase in (('数据校验',0),('供需分析',1),('目标确认',2)):
        if re.search(r'返回|回到|退回',text) and label in text:return {'type':'stage','phase':phase}
    if re.search(r'下达|发布.*APS', text, re.I): return {'type':'publish'}
    if re.search(r'导出', text): return {'type':'export'}
    if re.search(r'停止|取消运行', text): return {'type':'cancel'}
    if re.search(r'对比|比较', text): return {'type':'compare'}
    if re.search(r'历史|上次|最近.*生产', text): return {'type':'history', 'query':text}
    if re.search(r'产线负荷|产能利用', text) and not re.search(r'改|设置|阈值|目标', text): return {'type':'load'}
    if text.strip() in ('修改产量','编辑产量','新建版本','填写成品分解数量','编辑成品分解数量'):return {'type':'new_version'}
    if re.search(r'解释|为什么|为何|原因|没排|未排|决策',text):return {'type':'explain','query':text}
    # Modifications must be checked before query words like "产量" or "库存".
    pid = re.search(r'(?<!\d)\d{7}(?!\d)', text)
    product = pid[0] if pid else None
    edit = re.search(r'(?:改为|改成|调整为|调整到|设为|设置为|增加到|减少到|改到)\s*(\d+(?:\.\d+)?)\s*(?:吨|t)', text, re.I)
    delta = re.search(r'(增加|减少)\s*(\d+(?:\.\d+)?)\s*(?:吨|t)', text, re.I)
    if edit or delta:
        if len(re.findall(r'(?<!\d)\d{7}(?!\d)',text))>1 or re.search(r'\d\s*%|最多.*轮|号?线|\d{1,2}[:：]\d{2}',text):
            return {'type':'help','unrecognized':True}
        return {'type':'quantity','product':product,'query':text,'quantity':float(edit[1]) if edit else float(delta[2]),
                'kind':'new' if '新增' in text else 'total', 'delta':(1 if delta[1]=='增加' else -1) if delta and not edit else 0,
                'freeze_others': bool(re.search(r'其他.*不变|其余.*不变',text))}
    if re.search(r'解除|解锁|取消锁定', text): return {'type':'unlock','product':product,'query':text}
    if re.search(r'停用|禁用|启用|恢复.*线', text):
        if re.search(r'\d{1,2}[:：]\d{2}|小时|上午|下午|晚上|早班|晚班',text):return {'type':'help','unrecognized':True}
        match=re.search(r'([1-7一二三四五六七])\s*号?线',text)
        if match:return {'type':'line','line_id':str('一二三四五六七'.index(match[1])+1) if match[1] in '一二三四五六七' else match[1], 'disabled':bool(re.search(r'停用|禁用',text))}
    if re.search(r'安排|放到|移到|移至|锁定.*线|开始|完成',text) and (product or re.search(r'这个|该物料|它',text)):
        placement={}
        match=re.search(r'([1-7一二三四五六七])\s*号?线',text)
        if match:placement['line_id']=str('一二三四五六七'.index(match[1])+1) if match[1] in '一二三四五六七' else match[1]
        if re.search(r'\d{1,2}[:：]\d{2}',text):
            if len(re.findall(r'\d{1,2}[:：]\d{2}',text))>1:return {'type':'help','unrecognized':True}
            key='latest' if re.search(r'完成|结束|之前.*交',text) else 'earliest' if re.search(r'不早于|之后|以后',text) else 'start_minute'
            placement[key]=minute(text)
            if key=='latest' and placement[key]==0:placement[key]=1440
        if placement:return {'type':'placement','product':product,'query':text,'placement':placement}
    if re.search(r'覆盖',text):
        match=re.search(r'(非常规[12]|常规[12]).*?(\d+)\s*天',text)
        if match:return {'type':'config','patch':{'coverage':{match[1]:int(match[2])}}}
    thresholds={}
    for pattern,key in [('OEE','oee'),('均衡','balance'),('谷电','valley'),('粒径','particle'),('交付|满足率','delivery'),('可行性','feasibility')]:
        match=re.search('(?:'+pattern+r').{0,12}?(\d+(?:\.\d+)?)\s*%',text,re.I)
        if match:thresholds[key]=float(match[1])
    if thresholds:return {'type':'config','patch':{'thresholds':thresholds}}
    match=re.search(r'(?:最多|最大|上限|循环)\s*(\d+)\s*轮',text)
    if match:return {'type':'config','patch':{'max_rounds':int(match[1])}}
    if re.search(r'优先|侧重|重点|减少换型|均衡优先|交付优先',text):
        for pattern,focus in [('换型','setup'),('粒径','particle'),('谷电','valley'),('均衡','balanced'),('交付','delivery')]:
            if pattern in text:return {'type':'focus','focus':focus}
    if re.search(r'解释|为什么|为何|原因|没排|未排|决策',text):return {'type':'explain','product':product,'query':text}
    if re.search(r'需求|库存|供应|缺口|查.*物料',text):return {'type':'query','query':text,'product':product,'inventory':'库存' in text}
    if re.search(r'确认.*求解|开始求解|开始优化|重新排程|重新排产|重排|再次优化|新一轮',text):return {'type':'run'}
    if re.search(r'继续|确认目标|输出方案|通过校验|查看方案',text):return {'type':'continue'}
    if re.search(r'开始排产|帮我排|排一下|排个计划',text):return {'type':'start','goal':text}
    if re.search(r'你好|您好|帮助|怎么用|能做什么',text):return {'type':'help'}
    if re.search(r'不拆分|整批|铁律|批次',text):return {'type':'rules'}
    return None


def interpret(text, state):
    """Cloud only maps language to a bounded command. Facts/changes are executed locally."""
    from agentcore_client import load_config, call_agent, stop_agent
    from agent_worker import extract_json
    endpoint,key=load_config(BASE/'.env')
    instructions=(BASE/'prompts/conversation.md').read_text(encoding='utf-8')
    context={'message':text,'last_product':state.get('last_product'),'phase':state['phase'],
             'has_result':bool(state.get('selected_job_id')), 'recent_messages':state['messages'][-6:],
             'products':[{'id':k,'name':v['name']} for k,v in state['data']['demands'].items()]}
    run_id='chat-'+uuid.uuid4().hex
    thread_id='aps-chat-'+state['id']
    try:
        reply=call_agent(endpoint,key,instructions+'\n'+json.dumps(context,ensure_ascii=False)+'\n/no_think',thread_id=thread_id,run_id=run_id,timeout=60)
        texts=[v for v in reply.messages.values() if v.strip()]
        return extract_json(texts[-1]) if texts else {'type':'help'}
    except Exception:
        try:stop_agent(endpoint,key,thread_id,run_id)
        except Exception:pass
        return {'type':'help','unrecognized':True}


class Conversations:
    def __init__(self, manager):
        self.manager=manager
        self.lock=threading.RLock()
        self.states={}
        SESSIONS.mkdir(exist_ok=True)

    def save(self,s):
        s['updated_at']=datetime.now().isoformat(timespec='seconds')
        temp=SESSIONS/(s['id']+'.tmp')
        temp.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(SESSIONS/(s['id']+'.json'))
        self.states[s['id']]=s

    def load(self,sid):
        if not isinstance(sid,str) or not re.fullmatch('[0-9a-f]{32}',sid):raise KeyError('未知会话')
        if sid not in self.states:
            path=SESSIONS/(sid+'.json')
            if not path.exists():raise KeyError('未知会话')
            self.states[sid]=json.loads(path.read_text(encoding='utf-8'))
        s=self.states[sid]
        s.setdefault('current_stage',{'validation':0,'demand':1,'inventory':1,'goals':2}.get(s['view']['type'],s['phase']))
        enrich_snapshot(s['data'])
        return s

    def say(self,s,text):
        s['messages'].append(dict(role='agent',text=text,time=datetime.now().strftime('%H:%M:%S')))

    def create(self,config=None,job_id=None):
        with self.lock:
            cfg=checked_config(config or {})
            data=load_dataset(cfg['coverage'])
            s=dict(id=uuid.uuid4().hex,config=cfg,data=data,plan=default_plan(data),constraints=checked_constraints(data),
                   messages=[],phase=-1,current_stage=-1,view={'type':'welcome'},versions=[],selected_job_id=None,active_job_id=None,
                   completed_jobs=[],last_product=None,dirty=False,goal='满足交付与整批约束，综合优化六项指标。')
            self.say(s,'你好，我是排产智能体。已读取你提供的 Excel 数据：7 条产线、物料需求、供应及在制信息。\n通过对话完成“校验 → 供需 → 目标 → 求解 → 校验 → 输出”，每个阶段都可以查看和调整。\n方案完成后，可修改产量、调整约束、拖动甘特块，再启动新一轮优化。数据是本地快照，并非工厂实时连接。')
            if job_id:self.select(s,job_id,importing=True)
            self.save(s)
            return self.public(s)

    def refresh(self,s):
        jid=s.get('active_job_id')
        if not jid:return
        job=self.manager.get(jid)
        if job['status'] in ACTIVE:return
        s['active_job_id']=None
        if jid not in s['completed_jobs']:
            s['completed_jobs'].append(jid)
            result=best(job)
            s['phase']=5
            s['current_stage']=5
            s['view']={'type':'result'}
            if result and result['evaluation']['eligible']:
                s['selected_job_id']=jid
                s['plan']=copy.deepcopy(result['plan'])
                s['constraints']=checked_constraints(s['data'],job.get('constraints'))
                s['dirty']=False
                self.say(s,f"方案已输出：{result['evaluation']['scheduled_count']} 个任务，{result['evaluation']['total_quantity']:g} 吨，评分 {result['evaluation']['score']:.2f}。保留第 {result['number']} 轮。\n{job['stop_reason']}\n你可以查看负荷、解释物料、修改产量后重新优化，或导出方案。")
            else:
                self.say(s,'本轮未得到通过全部约束检查的方案；保留此前版本和修改草稿。'+str(job['stop_reason'])+'\n请查看冲突说明，调整约束后重排。')
            self.save(s)

    def present_job(self,jid):
        job=self.manager.get(jid)
        path=RUNS/jid/'snapshot.json'
        if path.exists():
            try:data=json.loads(path.read_text(encoding='utf-8'))
            except json.JSONDecodeError:return job  # The first snapshot may still be being written.
            for result in job['rounds']:
                if result.get('evaluation'):
                    result['order_details']=order_details_for(data,result)
        return job

    def public(self,s):
        versions=[]
        for jid in s['versions']:
            j=self.manager.get(jid);r=best(j)
            versions.append(dict(id=jid,status=j['status'],score=r['evaluation']['score'] if r else None,
                                 eligible=r['evaluation']['eligible'] if r else False,parent_job_id=j.get('parent_job_id'),goal=j['goal']))
        jid=s.get('active_job_id') or s.get('display_job_id') or s.get('selected_job_id')
        # A completed current run remains visible, including its infeasibility diagnostics.
        view=copy.deepcopy(s['view'])
        data=s['data']
        solved=self.manager.get(s['selected_job_id']) if s.get('selected_job_id') and not s['dirty'] else None
        confirmed=data['demands'] if s.get('selected_job_id') else s['constraints']['quantities']
        products=rows_for(data,s['plan'],s['config'],best(solved) if solved else None,confirmed)
        if view['type'] in ('demand','inventory'):
            ids=view.get('ids',list(data['demands']))
            view['rows']=[p for p in products if p['id'] in ids]
            view['is_detail']=len(ids)<len(data['demands'])
        if view['type']=='validation':view.update(warnings=data['warnings'],assumptions=data['assumptions'],counts={'物料':len(data['materials']),'供需':len(data['demands']),'产线':len(data['lines']),'产能记录':sum(map(len,data['capacities'].values()))})
        if view['type']=='history':view['rows']=self.manager.history()
        return {k:copy.deepcopy(s[k]) for k in ('id','messages','phase','current_stage','config','constraints','dirty','last_product','selected_job_id','active_job_id','goal')} | dict(
            view=view,versions=versions,job=self.present_job(jid) if jid else None,
            products=products,
            chips=self.chips(s))

    def chips(self,s):
        if s['active_job_id']:return ['查看需求概况','解释当前方案','停止本轮优化']
        p=s['current_stage']
        if p==0:return ['继续：供需分析','查看需求概况']
        if p==1:return ['上一步：数据校验','继续：确认排产目标']
        if p==2:return ['上一步：供需分析','确认目标，开始求解','优先改善产线均衡']
        if s['dirty'] and s['selected_job_id']:return ['确认修改，开始优化','查看需求概况','对比方案']
        if s['phase']<0:return ['开始排产','先看一下需求概况','查看历史排产记录']
        if s['phase']==0:return ['继续：供需分析','查看需求概况']
        if s['phase']==1:return ['继续：确认排产目标','优先减少换型']
        if s['phase']==2:return ['确认目标，开始求解','优先改善产线均衡']
        return ['修改产量','重新排程','解释方案','查看产线负荷','导出排产单','下达 APS']

    def get(self,sid):
        with self.lock:
            s=self.load(sid);self.refresh(s);return self.public(s)

    def history(self):
        with self.lock:
            values=[]
            for path in SESSIONS.glob('*.json'):
                try:
                    s=self.load(path.stem)
                    values.append({k:s[k] for k in ('id','goal','updated_at')} |
                                  dict(messages=len(s['messages']),versions=len(s['versions'])))
                except (ValueError,KeyError,OSError):
                    continue
            return sorted(values,key=lambda v:v['updated_at'],reverse=True)

    def resolve(self,s,action,required=False):
        data=s['data'];pid=action.get('product');text=action.get('query','')
        if pid:
            if pid not in data['demands']:raise ValueError(f'找不到可排产物料 {pid}。请使用 Excel 的7位物料编号。')
            ids=[pid]
        else:
            ids=[p for p in data['demands'] if p in text]
            if not ids:
                ids=[p for p,d in data['demands'].items() if d['name'] in text]
            if not ids:
                m=re.search(r'(?:查询|查看|查一下|查|解释)\s*(.+?)(?:的|需求|库存|供需|产量|为什么|$)',text)
                term=m[1].strip() if m else ''
                if term and term not in ('明天','今天','当前','一下','全部','所有','方案','物料','需求概况','一下需求概况','一下需求'):
                    ids=[p for p,d in data['demands'].items() if term in d['name']]
                    if not ids and not required:raise ValueError(f'未找到名称包含“{term}”的物料，请使用物料编号或名称关键词。')
            if not ids and re.search(r'这个|该物料|它|这个料',text) and s.get('last_product'):ids=[s['last_product']]
        if required and not ids and not text and s.get('last_product'):ids=[s['last_product']]
        if required and len(ids)!=1:raise ValueError('请指定一个物料编号；也可以先查询某个物料，再说“把它改成30.8吨”。')
        if len(ids)==1:s['last_product']=ids[0]
        return ids

    def select(self,s,jid,importing=False):
        if s.get('active_job_id'):raise ValueError('运行期间可以查询，切换或修改版本请先停止当前任务。')
        if not importing and jid not in s['versions']:raise ValueError('该版本不属于当前会话。')
        job=self.manager.get(jid);result=best(job)
        if job['status'] in ACTIVE or not result:raise ValueError('该任务尚无可载入的最终结果。')
        raw=json.loads((RUNS/jid/'snapshot.json').read_text(encoding='utf-8'))
        # Reload original quantity menus, which are intentionally narrowed in solver snapshots.
        data=load_dataset(job['config']['coverage'])
        if data['fingerprints']!=raw['fingerprints']:raise ValueError('Excel 数据已变化，不能将旧版本混入新快照，请新建会话。')
        if not result['evaluation']['eligible'] and not importing:
            s['display_job_id']=jid;s['view']={'type':'result'};return
        if not result['evaluation']['eligible']:
            raise ValueError('该记录未通过可行性检查，不能作为已完成方案载入修改。请查看原运行诊断。')
        s.update(data=data,config=copy.deepcopy(job['config']),plan=copy.deepcopy(result['plan']),
                 constraints=checked_constraints(data,job.get('constraints')),selected_job_id=jid,display_job_id=jid,
                 phase=5,current_stage=5,view={'type':'result'},dirty=False,goal=job['goal'])
        if jid not in s['versions']:s['versions'].append(jid)
        self.say(s,'已载入历史版本。后续修改会从这个版本创建新方案，原方案保留。')

    def message(self,sid,text='',action=None):
        if not isinstance(text,str) or len(text)>2000:raise ValueError('消息须为不超过2000字的文本。')
        with self.lock:
            original=self.load(sid);self.refresh(original)
            s=copy.deepcopy(original)
            if action is None:action=detect(text,s)
            if action is None:
                # Bound cloud interpretation; no tools or business facts are accepted from it.
                action=interpret(text,s)
            if not isinstance(action,dict):raise ValueError('对话操作格式错误。')
            s['messages'].append(dict(role='user',text=text or self.action_label(action),time=datetime.now().strftime('%H:%M:%S')))
            try:self.execute(s,action)
            except (ValueError,KeyError,TypeError) as exc:
                # Atomic drafts: a rejected edit never partially modifies plan/config/constraints.
                original['messages']=s['messages']
                self.say(original,'未应用本次操作：'+str(exc))
                self.save(original)
                return self.public(original)
            self.save(s)
            return self.public(s)

    def action_label(self,a):
        return {'quantity':'修改产量','edit':'保存产量与约束草稿','run':'确认方案并开始新一轮优化','config':'保存排产配置','select':'查看方案版本','query':'查询需求','explain':'解释物料','placement':'调整甘特图位置','new_version':'新建方案草稿','stage':'查看阶段','suggest':'采纳优化建议'}.get(a.get('type'),'操作')

    def execute(self,s,a):
        kind=a.get('type');data=s['data']
        if kind in ('quantity','edit','placement','unlock','config','line','focus','run','start','select','new_version','suggest') and s['active_job_id']:
            raise ValueError('当前优化仍在运行。可继续查询或解释，修改请先停止并等待任务结束。')
        if kind=='query':
            ids=list(data['demands']) if a.get('all') else self.resolve(s,a)
            for term in ('蛋鸭','肉鸭','小鸭','肉鸡','大鸡','母猪','小猪','大猪','草鱼','罗非鱼'):
                if not ids and term in a.get('query',''):
                    ids=[p for p,d in data['demands'].items() if term in d['name']]
                    if not ids:raise ValueError(f'未找到名称包含“{term}”的物料。')
            if not ids and re.search(r'\d{7}',a.get('query','')):raise ValueError('该物料不在可关联供需清单中。')
            ids=ids or list(data['demands'])
            s['view']={'type':'inventory' if a.get('inventory') else 'demand','ids':ids}
            if s['phase']>=0:s['current_stage']=1;s['phase']=max(1,s['phase'])
            if len(ids)==1:
                d=data['demands'][ids[0]]
                filled=s['selected_job_id'] or d['id'] in s['constraints']['quantities']
                quantity=f"{s['plan'][d['id']]['quantity']+d['wip']:g} 吨" if filled else '待填写'
                self.say(s,f"{d['id']} · {d['name']}\nT～T+7逐日需求：{d['daily']} 吨；当前覆盖 {d['cover_days']} 天，共 {d['cover_demand']:g} 吨。\n供应 {d['supply']:g} 吨（含在制 {d['wip']:g} 吨），库存上下限 {d['safety']:g}～{d['maximum']:g} 吨。必须新增至少 {d['minimum']:g} 吨。\n成品分解数量：{quantity}；当前一物料一行，计划产量与其相等。")
            else:self.say(s,f"已查询 {len(ids)} 个物料，右侧展示逐日需求、供应和生产草稿。查询不会启动求解。")
        elif kind in ('quantity','edit','placement','unlock'):
            if kind=='edit':
                changes=a.get('changes',[])
                if not isinstance(changes,list) or len(changes)>len(s['plan']):raise ValueError('修改清单无效。')
                if not changes:raise ValueError('没有修改项。')
            else:changes=[a]
            notes=[]
            for item in changes:
                pid=self.resolve(s,item,required=True)[0]
                mode=item.get('type',kind)
                if mode not in ('quantity','placement','unlock'):raise ValueError('不支持的修改操作。')
                if mode=='quantity':
                    q=item.get('quantity')
                    if type(q) not in (int,float):raise ValueError('产量必须为数字。')
                    if item.get('delta') and not s['selected_job_id'] and pid not in s['constraints']['quantities']:
                        raise ValueError('该物料尚未填写成品分解数量，请先填写明确数量，再增加或减少。')
                    if item.get('delta'):q=s['plan'][pid]['quantity']+q*item['delta']
                    elif item.get('kind','total')=='total':q-=data['demands'][pid]['wip']
                    q=round(q,4)
                    if item.get('freeze_others'):
                        if not s['selected_job_id']:raise ValueError('尚无完整方案，不能锁定其他未填写物料的数量。')
                        s['constraints']['quantities'].update({p:v['quantity'] for p,v in s['plan'].items()})
                    s['plan'][pid]['quantity']=q;s['constraints']['quantities'][pid]=q
                    notes.append(f"{pid}：成品分解数量 {q+data['demands'][pid]['wip']:g} 吨 → 计划产量 {q+data['demands'][pid]['wip']:g} 吨（含固定在制 {data['demands'][pid]['wip']:g} 吨）")
                elif mode=='placement':
                    if not s['selected_job_id']:raise ValueError('请先完成一版排程，再修改排程位置。')
                    s['constraints']['placements'].setdefault(pid,{}).update(item.get('placement',{}))
                    notes.append(f'{pid} 已设置产线/时间位置约束')
                else:
                    s['constraints']['quantities'].pop(pid,None);s['constraints']['placements'].pop(pid,None)
                    notes.append(f'{pid} 已解除人工锁定，恢复在合法范围内优化')
            s['constraints']=validate_revision(data,s['plan'],s['constraints']);s['dirty']=True
            if s['selected_job_id']:
                s['view']={'type':'result'}
                ending='原排程仍保留，尚未计算新的开始结束时间。点击“确认修改，开始优化”后生成新版本；人工填写的数量在整个 loop 中保持不变。'
            else:
                s['view']={'type':'demand'};s['current_stage']=1;s['phase']=max(1,s['phase'])
                ending='已同步汇总到计划产量，尚未启动排程。填写的物料锁定数量；未填写的物料在确认求解后由基准及 Agent 优化生成。'
            self.say(s,'已保存成品分解数量：\n'+'\n'.join(notes)+'\n'+ending)
        elif kind=='config':
            patch=a.get('patch')
            if not isinstance(patch,dict):raise ValueError('配置修改格式错误。')
            config=copy.deepcopy(s['config'])
            for k,v in patch.items():
                if k in ('coverage','thresholds') and isinstance(v,dict):config[k].update(v)
                else:config[k]=v
            config=checked_config(config)
            if config['coverage']!=s['config']['coverage']:
                fresh=load_dataset(config['coverage'])
                if fresh['fingerprints']!=data['fingerprints']:raise ValueError('Excel已变化，请新建会话读取新数据。')
                s['data']=data=fresh
                if not s['selected_job_id']:
                    s['plan']=default_plan(data)
                    for pid,q in s['constraints']['quantities'].items():s['plan'][pid]['quantity']=q
            validate_revision(data,s['plan'],s['constraints'])
            s['config']=config;s['dirty']=True;s['view']={'type':'goals'}
            s['current_stage']=2;s['phase']=max(2,s['phase'])
            self.say(s,'已更新下一轮配置。指标百分比是 loop 的停止目标；需求满足、整批和物料不拆线仍为硬约束。点击确认后才开始计算。')
        elif kind=='line':
            lid=a.get('line_id')
            if lid not in data['lines']:raise ValueError('未知产线。')
            disabled=set(s['constraints']['disabled_lines'])
            if a.get('disabled'):disabled.add(lid)
            else:disabled.discard(lid)
            s['constraints']['disabled_lines']=sorted(disabled)
            validate_revision(data,s['plan'],s['constraints']);s['dirty']=True;s['view']={'type':'goals'}
            s['current_stage']=2;s['phase']=max(2,s['phase'])
            self.say(s,f"已{'停用' if a.get('disabled') else '恢复'}{data['lines'][lid]['name']}，作用于下轮完整排产日。原版本保留。")
        elif kind in ('focus','suggest'):
            focus=a.get('focus')
            if focus not in FOCUSES:raise ValueError('未知优化方向。')
            s['constraints']['focus']=focus;s['dirty']=True;s['view']={'type':'goals'}
            s['current_stage']=2;s['phase']=max(2,s['phase'])
            self.say(s,'下一轮优先'+FOCUSES[focus]+'。这会改变求解目标权重，实际是否改善以重算结果为准。')
        elif kind in ('run','start','continue','stage','back','next_stage'):
            if kind=='next_stage':
                p=a.get('phase')
                if p!=2 or s['phase']<1:raise ValueError('请先完成数据校验并查看供需分析，再确认目标。')
                s['phase']=max(s['phase'],p);s['current_stage']=p;s['view']={'type':'goals'}
                self.say(s,'已进入目标确认。可返回供需分析；只有点击确认开始求解才会运行。');return
            if kind in ('stage','back'):
                p=max(0,s['current_stage']-1) if kind=='back' else a.get('phase')
                if type(p) is not int or p<0 or p>5:raise ValueError('未知阶段。')
                if p>s['phase']:raise ValueError('该阶段尚未执行，请先继续当前流程。')
                s['current_stage']=p
                s['view']={'type':{0:'validation',1:'demand',2:'goals',3:'result',4:'result',5:'result'}[p]};return
            if kind=='start':
                s['goal']=a.get('goal') or s['goal'];s['phase']=max(0,s['phase']);s['current_stage']=0;s['view']={'type':'validation'}
                self.say(s,'已创建排产草稿，完成数据读取和关联校验。右侧列出数据口径及已知说明。下一步查看供需。');return
            if kind=='continue' and s['current_stage']<2:
                p=max(0,s['current_stage']+1);s['current_stage']=p;s['phase']=max(s['phase'],p)
                s['view']={'type':{0:'validation',1:'demand',2:'goals'}[p]}
                self.say(s,'供需分析已完成。查看需求缺口与补货目标后，可继续确认优化目标。' if p==1 else '请确认覆盖期、指标目标、循环上限和优化方向；确认后执行真实求解。');return
            if kind=='continue' and s['current_stage']==2:
                s['view']={'type':'goals'};self.say(s,'当前是目标确认。请点击“确认目标，开始求解”后才会计算；也可返回上一步继续查看供需。');return
            if kind=='continue':
                s['view']={'type':'result'};self.say(s,'右侧展示当前方案与校验结果。需要新版本时请说“重新排程”。');return
            self.run(s)
        elif kind=='select':self.select(s,a.get('job_id'))
        elif kind=='new_version':
            s['view']={'type':'editor'}
            if s['selected_job_id']:self.say(s,'已从当前版本打开成品分解数量编辑表。修改后自动汇总计划产量，保存草稿，再确认开始新一轮优化。')
            else:
                s['current_stage']=1;s['phase']=max(1,s['phase'])
                self.say(s,'请填写成品分解数量，计划产量自动汇总。当前每物料一条分解行；未填写的物料由后续排产生成数量。保存不会启动求解。')
        elif kind=='explain':self.explain(s,a)
        elif kind=='compare':
            s['view']={'type':'compare'};self.say(s,'右侧按真实运行结果比较版本。不同产量或约束的版本可对照，但分数差异不代表相同问题下的纯算法提升。')
        elif kind=='history':
            s['view']={'type':'history'}
            ids=self.resolve(s,a)
            if len(ids)==1:
                pid=ids[0];lines=[v['name'] for v in data['lines'].values() if v['previous_product']==pid]
                self.say(s,f"{pid} 在本次快照中{'是'+ '、'.join(lines)+'的上次末生产物料' if lines else '不是各产线上次末生产物料'}。\nExcel没有历史实际生产日期明细；右侧可查询本地已计算的方案，不能等同于车间已执行记录。")
            else:self.say(s,'右侧是本地真实运行记录，可载入继续修改。暂未接入工厂历史执行记录接口。')
        elif kind=='load':s['view']={'type':'load'};self.say(s,'右侧显示当前排程的各产线生产时长、换型时长和负荷。')
        elif kind=='export':s['view']={'type':'export'};self.say(s,'可导出当前版本的排程 CSV 和完整 JSON。未重算的草稿不会覆盖已求解结果。')
        elif kind=='publish':
            self.say(s,'尚未接入 APS 下达接口，当前不会下达或写入生产系统。需要提供下达接口地址、鉴权方式、请求字段和成功回执格式。现在可以导出 CSV / JSON 供审核。')
        elif kind=='cancel':
            if s['active_job_id']:
                job=self.manager.jobs.get(s['active_job_id'])
                if job:job.cancel.set()
                self.say(s,'已请求停止。等待计算结束后保留已完成的最佳方案，可再修改。')
            else:self.say(s,'当前没有运行中的任务。')
        elif kind=='rules':
            self.say(s,'发货缺口必须覆盖，物料不跨线拆分，新增产量按混合机批次取整；在制余量固定且不取整。可调整覆盖期、指标目标、优化方向、产线启停及物料位置约束。订单级交期、检修时段与真实电价需要相应业务数据。')
        elif kind=='help':
            self.say(s,('这句话没有解析为可执行操作，未修改任何参数。\n' if a.get('unrecognized') else '')+'可以说：\n“查询5000611的需求”\n“把它的总产量改成30.8吨”\n“5000611安排在二号线，08:00开始”\n“OEE目标改为85%”\n“常规1覆盖2天”\n“优先减少换型”\n“重新排程”\n也可以通过右侧编辑表修改，保存后确认优化。')
        else:raise ValueError('暂不支持该操作，请使用需求查询、修改产量、配置约束、重排或解释。')

    def run(self,s):
        if s['active_job_id']:raise ValueError('已有优化运行中。')
        constraints=validate_revision(s['data'],s['plan'],s['constraints'])
        revision=dict(data=s['data'],plan=s['plan'],constraints=constraints,parent_job_id=s['selected_job_id'],session_id=s['id'])
        job=self.manager.start(s['config'],s['goal'],revision)
        s['active_job_id']=job['id'];s['display_job_id']=job['id'];s['versions'].append(job['id']);s['phase']=max(3,s['phase']);s['current_stage']=3;s['view']={'type':'result'}
        mode='先计算已确认计划，再由 Agent 在用户锁定约束内迭代优化。' if s['config']['mode']=='agent' and s['config']['max_rounds']>1 else '本次只求解一次已确认计划，不调用排产 Agent。'
        self.say(s,f"已启动 v{len(s['versions'])}。{mode}最多 {s['config']['max_rounds']} 轮，{s['config']['time_budget']} 秒；保留本轮最佳及所有历史版本。")

    def explain(self,s,a):
        jid=a.get('job_id') or s.get('active_job_id') or s.get('display_job_id') or s['selected_job_id']
        if not jid:raise ValueError('先完成排程后，才能解释实际结果。')
        if jid not in s['versions']:raise ValueError('该版本不属于当前会话。')
        job=self.manager.get(jid);result=best(job)
        if a.get('round') is not None:
            result=next((r for r in job['rounds'] if r['number']==a['round'] and r.get('evaluation')),None)
        if not result:raise ValueError('当前尚未得到可解释的求解结果。')
        data=json.loads((RUNS/jid/'snapshot.json').read_text(encoding='utf-8'))
        ids=self.resolve(s,a);e=result['evaluation']
        if len(ids)==1:
            pid=ids[0];d=data['demands'][pid];p=result['plan'][pid]
            row=next((r for r in e['rows'] if r['product_id']==pid),None)
            text=f"{pid} · {d['name']}\n覆盖需求 {d['cover_demand']:g} − 供应 {d['supply']:g}，必须新增至少 {d['minimum']:g} 吨；补到安全库存的目标为 {d['target']:g} 吨。\n本方案新增 {p['quantity']:g} 吨，在制 {d['wip']:g} 吨。"
            if row:
                cap=data['capacities'][pid][row['line_id']]
                text+=f"\n安排到{row['line_name']}，产能 {cap['rate']:g} 吨/小时，生产 {row['duration']:g} 分钟，前置换型 {row['switch']:g} 分钟。"
                text+='\n可选产线：'+ '；'.join(f"{data['lines'][lid]['name']} {c['rate']:g}吨/小时，偏好优先级{c['priority']}" for lid,c in data['capacities'][pid].items())
                text+='\n这是求解器在当前数量、可用产线、换型和均衡目标下得到的可行安排；没有做逐一反事实求解，不能断言其他产线一定更差。'
            else:
                text+='\n'+('供应已覆盖当前需求，计划新增为0，且没有待完成在制。' if p['quantity']+d['wip']==0 else '有计划但未排入。请看约束冲突与产线容量；现有求解器未返回可证明的最小冲突集。')
            text+='\n人工锁定：'+str(job.get('constraints',{}).get('quantities',{}).get(pid,'无'))
            placement=job.get('constraints',{}).get('placements',{}).get(pid)
            if placement:
                labels=[]
                if placement.get('line_id'):labels.append('指定'+data['lines'][placement['line_id']]['name'])
                origin=datetime.fromisoformat(job['config']['schedule_date'])-timedelta(hours=4)
                for key,label in (('start_minute','固定开始'),('earliest','最早开始'),('latest','最晚完成')):
                    if key in placement:labels.append(label+' '+(origin+timedelta(minutes=placement[key])).strftime('%m-%d %H:%M'))
                text+='\n人工位置约束：'+'；'.join(labels)+'。该约束直接限制产线或时间选择。'
        else:
            text=f"正在解释第{result['number']}轮（本版最佳为第{job['best_round']}轮），评分 {e['score']:.2f}，{'通过' if e['eligible'] else '未通过'}可行性校验。\n"+'\n'.join(f"{m['name']}：{str(m['value'])+'%' if m['value'] is not None else '不适用'}；{m['note']}" for m in e['metrics'].values())
            text+='\n本轮提案理由：'+result['proposal']['reason']+'\n停止原因：'+str(job.get('stop_reason') or '仍在运行')
        if e['violations']:text+='\n校验问题：'+'；'.join(e['violations'][:12])
        text=f"方案 {jid[:8]} · 第 {result['number']} 轮 · {job['config']['schedule_date']}\n"+text
        s['view']={'type':'explain','text':text,'product':ids[0] if len(ids)==1 else None};self.say(s,text)
