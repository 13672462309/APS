"""The 81 reference columns, with explicit provenance and no invented source data."""
from datetime import datetime, timedelta

# key | label | width | source type | source / calculation
SPEC = '''order_demand|订单需求量|120|mapped|演示映射：2-供需数据的T需求，按物料汇总；没有客户订单拆分
plan_quantity|计划产量(吨)|150|computed|该物料各分解行的成品分解数量之和；需求确认当前一物料一行，未填写时为空
category|产品分类|100|excel|2-供需数据：产品分类
due_date|订单需求截止日期|130|missing|未提供客户订单交期；不以Demo统一交期冒充实际订单截止日期
shortage|缺口数量|140|computed|min(0，供应－本周期覆盖需求)；负数表示缺口，无缺口为0，尚未计入本次计划新增量
wip|继续生产量(吨)|140|excel|3-前置订单数据：继续生产量，按物料汇总
id|物料编号|100|excel|1-物料数据、2-供需数据：物料编号
decomposed_quantity|成品分解数量(吨)|150|computed|当前分解行的计划产量，可人工填写；需求确认一物料一行，填写后自动汇总到计划产量
suggested_quantity|建议生产量(吨)|150|computed|最近可行求解方案的总产量；尚未求解时为空，不用内部规则基准冒充已生成建议
future_order_demand|未来订单需求量|140|mapped|演示映射：T+1至T+7物料汇总需求之和；没有客户订单拆分
name|物料|360|excel|2-供需数据：物料名称
stock|库存数量|130|computed|按当前Demo供应已含在制口径：max(0，供应－在制余量)，并非独立仓库实盘
safety|库存最小值|100|excel|2-供需数据：库存最小值
maximum|库存最大值|100|excel|2-供需数据：库存最大值
semi|配料半成品|380|excel|1-物料数据：关联半成品编号及名称
remark|备注|140|missing|未提供业务备注
order_ids|生产订单号|100|excel|3-前置订单数据的已有订单号；不会生成或冒充正式生产订单号
decomposition_count|成品分解次数|100|computed|当前需求确认每物料一条分解行；未填写时为空
pending_quantity|待生产量(吨)|150|computed|当前已填写或已生成的待执行计划总量；未填写时为空
line_priority|排产优先级|120|excel|6-产能数据：各候选产线的排产优先级，数字越小越优先
workshop|车间|140|computed|当前选定可行方案的已排产线所属车间；修改草稿后待重算
line|产线|140|computed|当前选定可行方案的产线；没有排程或有待计算修改时为空
custom_product|订做料|100|missing|未提供订做料标识，不以产品分类推断
total_demand|总需求量|110|computed|现有需求窗口T至T+7的汇总需求之和
pickup_demand|提货需求量|120|missing|未提供提货需求拆分
future_pickup_demand|未来提货需求量|140|missing|未提供未来提货需求拆分
manual_stockup|手工备货量|120|missing|未提供独立手工备货量，不以人工调整后的总产量替代
combined_demand|拼单需求量|120|missing|未提供拼单需求拆分
future_combined_demand|未来拼单需求量|140|missing|未提供未来拼单需求拆分
supply|总供应量|130|excel|2-供需数据：供应，当前口径已含在制余量
frozen_stock|冻结库存量|130|missing|未提供冻结库存量
scheduled_quantity|当日已排产量|120|computed|当前选定可行方案已排入的总吨数；修改草稿后待重算，不代表已执行
min_quantity|最低生产量|140|computed|当前需求、库存和整批条件下合法新增量的最小值，不含在制
max_quantity|最高生产量|140|computed|当前需求、库存和整批条件下合法新增量的最大值，不含在制
reference_sales|参考日均销量|130|mapped|当前展示采用Excel前30天日均销量，未替代其他业务口径
last_year_sales|去年同期销量|130|missing|未提供去年同期销量
last_month_same_sales|上月同期销量|130|missing|未提供上月同期销量
month_daily_sales|当月日均销量|120|missing|未提供当月日均销量，不能由前30天日均销量替代
sales_7_total|前7日总销量|120|computed|按Excel前7天日均销量×7估算；原始日均可能已经舍入
sales_7_daily|前7日日均销量|120|excel|2-供需数据：前7天日均销量
sales_3_total|前3日总销量|120|missing|未提供前3日销量
sales_3_daily|前3日日均销量|120|missing|未提供前3日日均销量
last_month_total|上月销量汇总|130|missing|未提供上月销量汇总
month_actual|本月实际销量|120|missing|未提供本月实际销量
month_forecast|本月销售预测|120|missing|未提供本月销售预测
group3|物料组（三级）|220|excel|1-物料数据：三级物料组
group_description|物料组描述|160|missing|未提供独立物料组描述字典
product_group|产品组|110|missing|未提供产品组映射，不将二级物料组自动视为产品组
cycle|生产周期|80|excel|1-物料数据：生产周期，表示重复生产间隔天数
last_produced_at|最近生产日期|110|missing|上次末生产物料表不含实际生产日期
last_produced_quantity|最近生产数量|110|missing|未提供最近实际生产数量
warehouse|生产仓储地点|140|missing|未提供生产仓储地点
die|理论环模|120|missing|未提供环模型号，不以粒径替代型号
diameter|生产粒径|80|excel|1-物料数据：粒径；源数据为空时保持为空
open_ratio|开孔率(%)|110|missing|未提供环模开孔率
aspect_ratio|长径比|80|missing|未提供环模长径比
supply_type|供应类型|100|missing|未提供供应类型
package|包装类型|80|excel|1-物料数据：包装类型
preceding_orders|前置订单|100|excel|3-前置订单数据中关联当前物料的订单号
plan_number|生产计划号|150|missing|未提供正式生产计划编号；本地版本ID不充当生产计划号
core_demand|核心生产需求|130|computed|当前Demo需新增的最低缺口：max(0，本周期需求－供应)
replenishment_demand|补库生产需求|130|computed|当前新增计划中超过最低缺口的吨数，含整批尾差
demand_management|需求管理方式|130|mapped|按字段说明：常规1/2按库存生产，非常规1集中生产，非常规2按单生产
replenishment_strategy|补库策略|80|mapped|沿用当前Demo规则：补安全库存，或非常规2/零库存上下限仅补缺口
replenishment_flag|补库标识|80|computed|当前新增计划是否超过最低缺口；包含不可避免的整批尾差
planned_start|指导开工时间|160|computed|当前选定可行方案的计划开始时间；修改草稿后待重算
planned_end|指导完工时间|160|computed|当前选定可行方案的计划结束时间；修改草稿后待重算
day_demand_0|T订单需求量|160|mapped|演示映射：Excel T物料汇总生产需求，非客户订单明细
day_demand_1|T+1订单需求量|160|mapped|演示映射：Excel T+1物料汇总生产需求，非客户订单明细
day_demand_2|T+2订单需求量|160|mapped|演示映射：Excel T+2物料汇总生产需求，非客户订单明细
day_gap_0|T缺口|160|computed|截至T的累计需求减初始供应，取非负；不含本次计划新增供应
day_gap_1|T+1缺口|160|computed|截至T+1的累计需求减初始供应，取非负；不含本次计划新增供应
day_gap_2|T+2缺口|160|computed|截至T+2的累计需求减初始供应，取非负；不含本次计划新增供应
grade|档次|160|missing|未提供档次
process|生产对标料型|160|excel|1-物料数据：生产工艺（生产对标料型）
dosing|加药|100|excel|1-物料数据：加药标识
coloring|加色|100|excel|1-物料数据：加色标识
bags|包数|100|missing|未提供单包净重，不能从物料名称推断包数
stock_days|预计库存可用天数|140|computed|估算库存数量÷前7天日均销量；销量为0或缺失时不计算
period_demand|本周期需求量|190|computed|当前覆盖天数内的需求合计，与本次排产需求边界一致
next_period_demand|下周期需求量|190|computed|紧接当前覆盖期、相同天数的需求合计；超过T+7数据窗口时保持为空'''


def catalog():
    result=[]
    for line in SPEC.splitlines():
        key,label,width,source,note=line.split('|')
        result.append(dict(key=key,label=label,width=int(width),source=source,note=note,
            visible=key!='stock_days',frozen=key in ('id','name','period_demand','next_period_demand')))
    return result


def enrich_snapshot(data):
    """Only add display metadata to older snapshots when their files are unchanged."""
    if data.get('presentation_version'):return
    from data_loader import load_dataset
    fresh=load_dataset(data['coverage'])
    if fresh['fingerprints']==data['fingerprints']:
        data['wip_orders']=fresh['wip_orders']
        for pid,d in data['demands'].items():
            for key in ('avg_sales_7','avg_sales_30'):d[key]=fresh['demands'][pid][key]
    data['presentation_version']=1


def rows_for(data,plan,config,result=None,confirmed_ids=None):
    scheduled={r['product_id']:r for r in result['evaluation'].get('rows',[])} if result else {}
    confirmed=set(confirmed_ids or ())
    start=datetime.fromisoformat(config['schedule_date'])-timedelta(hours=4)
    records=[]
    for pid,d in data['demands'].items():
        m=data['materials'][pid];q=plan[pid]['quantity'];r=scheduled.get(pid)
        orders=data.get('wip_orders',{}).get(pid,[]);ids='、'.join(x['order_id'] for x in orders) or None
        total=round(q+d['wip'],4) if pid in confirmed else None
        sales7=d.get('avg_sales_7');sales30=d.get('avg_sales_30');cover=d['cover_days']
        replenishment=max(0,q-d['minimum']) if total is not None else None
        fields=dict(order_demand=d['daily'][0],plan_quantity=total,category=d['category'],shortage=-d['minimum'] if d['minimum']>0 else 0,
            decomposed_quantity=total,decomposition_count=1 if total is not None else None,
            wip=d['wip'],id=pid,suggested_quantity=round(result['plan'][pid]['quantity']+d['wip'],4) if result else None,future_order_demand=sum(d['daily'][1:]),
            name=d['name'],stock=d['stock'],safety=d['safety'],maximum=d['maximum'],semi=f"{m['semi']} · {m['semi_name']}",
            order_ids=ids,pending_quantity=total,
            line_priority='；'.join(f"{data['lines'][lid]['name']}：{cap['priority']}" for lid,cap in data['capacities'][pid].items()),
            workshop=data['lines'][r['line_id']]['workshop'] if r else None,line=r['line_name'] if r else None,
            total_demand=sum(d['daily']),supply=d['supply'],scheduled_quantity=r['quantity'] if r else (0 if result else None),
            min_quantity=min(d['options']),max_quantity=max(d['options']),reference_sales=sales30,
            sales_7_total=round(sales7*7,4) if sales7 is not None else None,sales_7_daily=sales7,
            group3=m['group3'],cycle=m['cycle'],diameter=m['diameter'],package=m['package'],preceding_orders=ids,
            core_demand=d['minimum'],replenishment_demand=round(replenishment,4) if replenishment is not None else None,
            demand_management={'常规1':'按库存生产','常规2':'按库存生产','非常规1':'集中生产','非常规2':'按单生产'}[d['category']],
            replenishment_strategy='仅补缺口' if d['category']=='非常规2' or (d['maximum']==0 and d['safety']==0) else '补安全库存',
            replenishment_flag=('是' if replenishment>1e-6 else '否') if replenishment is not None else None,
            planned_start=(start+timedelta(minutes=r['start'])).isoformat(' ',timespec='minutes') if r else None,
            planned_end=(start+timedelta(minutes=r['end'])).isoformat(' ',timespec='minutes') if r else None,
            process=m['process'],dosing='是' if m['dosing'] else '否',coloring='是' if m['coloring'] else '否',
            stock_days=round(d['stock']/sales7,2) if sales7 and sales7>0 else None,
            period_demand=d['cover_demand'],next_period_demand=sum(d['daily'][cover:2*cover]) if 2*cover<=len(d['daily']) else None)
        for offset in range(3):
            fields['day_demand_'+str(offset)]=d['daily'][offset]
            fields['day_gap_'+str(offset)]=round(max(0,sum(d['daily'][:offset+1])-d['supply']),4)
        fields={f['key']:fields.get(f['key']) for f in catalog()}
        records.append(dict(d,planned_new=q,quantity=q,priority=plan[pid]['priority'],capacities=data['capacities'][pid],fields=fields))
    return records
