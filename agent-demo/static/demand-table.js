'use strict';
/* The confirmed layout is shared by overview, details and the two data pages. */
window.DemandTable = (() => {
  const KEY='aps-demand-columns-v2';
  const LEGACY_KEY='aps-demand-columns-v1';
  const sources={excel:'Excel',computed:'计算',mapped:'口径映射',missing:'缺数据'};
  const compact=new Set(['id','name','category','order_demand','plan_quantity','shortage','wip','supply','safety','maximum','period_demand','line']);
  let settingsDrag=null;

  function label(field){
    const match=/^day_(demand|gap)_(\d)$/.exec(field.key);
    if(!match)return field.label;
    const date=new Date(config.schedule_date+'T12:00:00');date.setDate(date.getDate()+Number(match[2]));
    return `${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}${match[1]==='demand'?'订单需求量':'缺口'}`;
  }
  const identifiersFirst=layout=>[
    ...['id','name'].map(key=>layout.find(c=>c.key===key)).filter(Boolean).map(c=>({...c,visible:true})),
    ...layout.filter(c=>!['id','name'].includes(c.key))
  ];
  const defaults=()=>identifiersFirst(boot.demand_fields.map(f=>({key:f.key,visible:f.visible,frozen:f.frozen,width:f.width})));
  function normalize(raw){
    const fields=new Map(boot.demand_fields.map(f=>[f.key,f])),seen=new Set(),layout=[];
    for(const c of Array.isArray(raw)?raw:[]){
      if(!c||!fields.has(c.key)||seen.has(c.key))continue;
      const f=fields.get(c.key);seen.add(c.key);
      layout.push({key:c.key,visible:typeof c.visible==='boolean'?c.visible:f.visible,
        frozen:typeof c.frozen==='boolean'?c.frozen:f.frozen,
        width:Number.isInteger(c.width)&&c.width>=60&&c.width<=600?c.width:f.width});
    }
    for(const f of defaults())if(!seen.has(f.key))layout.push(f);
    return layout.some(f=>f.visible)?layout:defaults();
  }
  function saved(){
    try{
      const current=localStorage.getItem(KEY);
      if(current!==null)return normalize(JSON.parse(current));
      // Migrate once, preserving all other column preferences and later manual reordering.
      const layout=identifiersFirst(normalize(JSON.parse(localStorage.getItem(LEGACY_KEY))));
      try{localStorage.setItem(KEY,JSON.stringify(layout))}catch{}
      return layout;
    }catch{return defaults()}
  }
  function visible(layout){return layout.filter(c=>c.visible).map(c=>({...boot.demand_fields.find(f=>f.key===c.key),...c}))}
  function valueText(value){return value==null?'—':typeof value==='number'?new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(value):String(value)}
  function move(list,key,target,after=false){
    if(key===target)return list;
    const from=list.findIndex(c=>c.key===key);if(from<0)return list;
    const next=list.slice(),[entry]=next.splice(from,1),to=next.findIndex(c=>c.key===target);
    if(to<0)return list;next.splice(to+(after?1:0),0,entry);return next;
  }

  function render(root,rows,inventory=false){
    root._demandResize?.disconnect();
    const isDetail=rows.length<session.products.length;
    root.innerHTML=`<div class="demo-card demand-card"><div class="demand-heading"><h2>${inventory?'库存与供应':'需求概况与生产草稿'}</h2><button class="demo-button" type="button" data-columns>列表设置</button></div>
      <p class="demo-muted">T = ${esc(config.schedule_date)}，共 ${rows.length} 个物料。填写成品分解数量，计划产量自动汇总；当前每物料一行，未填写时显示“—”。数量含固定在制。订单需求按 Excel 物料汇总映射；悬停列名可查看来源。</p>
      ${session.dirty?'<p class="demo-muted">存在修改草稿，产线、已排产量及开完工时间待重新求解。</p>':''}
      <div class="demand-toolbar"><input class="filter-input" data-filter aria-label="筛选物料编号或名称" placeholder="输入物料编号或名称筛选">
      ${isDetail?button('返回完整表单','query',{all:true,inventory},'primary'):''}<span class="demo-muted" data-layout-summary></span></div>
      ${!inventory?`<div class="demo-actions"><button type="button" class="demo-button primary" data-save-decomposition disabled>保存成品分解数量</button><button type="button" class="demo-button" data-open-decomposition ${active()?'disabled':''}>打开分解数量编辑表</button><span class="demo-muted" data-decomposition-status>人工填写的数量锁定；未填写的物料由后续排产生成。</span></div>`:''}
      <p class="demo-muted freeze-note" hidden data-freeze-note>窗口较窄，冻结列暂时取消固定，仍按确认顺序显示；加宽页面或减少冻结列可恢复固定。</p>
      <div class="demo-table-wrap demand-table-wrap" tabindex="0" aria-label="需求明细表，可横向滚动" data-demand-table></div>
      <div class="demo-actions demand-navigation">${session.current_stage>=0?button('上一步：数据校验','stage',{phase:0})+button('下一步：目标确认','next_stage',{phase:2},'primary'):button('开始排产','start',{},'primary')}</div></div>`;
    const filter=root.querySelector('[data-filter]'),wrap=root.querySelector('[data-demand-table]');
    let columns=[];const edits=new Map();
    const valueFor=(d,key)=>['decomposed_quantity','plan_quantity','pending_quantity'].includes(key)&&edits.has(d.id)?(edits.get(d.id)===''?null:Number(edits.get(d.id))):d.fields?.[key];
    const cell=(d,c)=>{
      const v=valueFor(d,c.key);
      const content=c.key==='decomposed_quantity'&&!inventory?`<input class="decomposition-input" type="number" min="${d.wip}" step="any" value="${v??''}" data-decomposition="${d.id}" aria-label="${d.id} 成品分解数量" placeholder="待填写" ${active()?'disabled':''}>`:esc(valueText(v));
      return `<td data-column="${c.key}" class="${typeof v==='number'?'number-cell':''}${c.key==='shortage'&&v<0?' shortage-negative':''}" title="${esc(v==null?'暂无数据：'+c.note:valueText(v))}">${content}</td>`;
    };
    const applyFreeze=()=>{
      const total=columns.filter(c=>c.frozen).reduce((n,c)=>n+c.width,0),enabled=total<=wrap.clientWidth-140;
      root.querySelector('[data-freeze-note]').hidden=enabled||total===0;
      let left=0;
      for(const c of columns){
        const pin=c.frozen&&enabled;
        wrap.querySelectorAll(`[data-column="${c.key}"]`).forEach(cell=>{
          cell.classList.toggle('frozen-column',pin);cell.style.left=pin?left+'px':'';
        });if(pin)left+=c.width;
      }
    };
    const draw=()=>{
      columns=visible(saved());const matched=rows.filter(d=>(d.id+d.name).includes(filter.value.trim()));
      const size=columns.reduce((n,c)=>n+c.width,110);
      root.querySelector('[data-layout-summary]').textContent=`显示 ${columns.length} / ${boot.demand_fields.length} 个字段 · ${matched.length} 条记录`;
      wrap.innerHTML=`<table class="demo-table demand-table" style="width:${size}px"><colgroup>${columns.map(c=>`<col style="width:${c.width}px">`).join('')}<col style="width:110px"></colgroup>
        <thead><tr>${columns.map(c=>`<th data-column="${c.key}" title="${esc(sources[c.source]+'：'+c.note)}">${esc(label(c))}</th>`).join('')}<th class="demand-actions-cell">操作</th></tr></thead>
        <tbody>${matched.length?matched.map(d=>`<tr>${columns.map(c=>cell(d,c)).join('')}
        <td class="demand-actions-cell">${button('查明细','query',{product:d.id,inventory})}</td></tr>`).join(''):`<tr><td colspan="${columns.length+1}">没有匹配的物料</td></tr>`}</tbody></table>`;
      bind(root);applyFreeze();
      wrap.querySelectorAll('[data-decomposition]').forEach(input=>input.oninput=()=>{
        const d=rows.find(d=>d.id===input.dataset.decomposition),original=d.fields.decomposed_quantity;
        if((input.value===''&&original==null)||(input.value!==''&&original!=null&&Math.abs(Number(input.value)-original)<1e-6))edits.delete(d.id);else edits.set(d.id,input.value);
        for(const key of ['plan_quantity','pending_quantity']){const td=input.closest('tr').querySelector(`[data-column="${key}"]`);if(td){td.textContent=valueText(valueFor(d,key));td.title=td.textContent}}
        root.querySelector('[data-save-decomposition]').disabled=!edits.size;
        root.querySelector('[data-decomposition-status]').textContent=edits.size?`${edits.size} 条分解数量待保存；当前显示为编辑预览。`:'人工填写的数量锁定；未填写的物料由后续排产生成。';
      });
      root.querySelectorAll('[data-action]').forEach(b=>{const click=b.onclick;b.onclick=()=>{if(edits.size){toast('请先保存成品分解数量，再切换步骤或物料。');return}click()}});
    };
    filter.oninput=draw;
    root.querySelector('[data-columns]').onclick=()=>openSettings(()=>draw());
    const save=root.querySelector('[data-save-decomposition]');
    if(save){
      save.onclick=async()=>{
        const changes=[];
        for(const [pid,value] of edits){
          if(value===''||!Number.isFinite(Number(value))){toast('请输入有效的成品分解数量；不生产时填写0。');return}
          changes.push({type:'quantity',product:pid,quantity:Number(value),kind:'total'});
        }
        save.disabled=true;const ok=await send('',{type:'edit',changes});
        if(!ok&&root.querySelector('[data-decomposition-status]'))toast('数量未保存，请按提示调整后重新填写。');
      };
      root.querySelector('[data-open-decomposition]').onclick=()=>{if(edits.size){toast('请先保存当前分解数量。');return}send('',{type:'new_version'})};
    }
    root._demandResize=new ResizeObserver(applyFreeze);root._demandResize.observe(wrap);
    draw();
  }

  function openSettings(onConfirm){
    document.getElementById('columnSettings')?.remove();
    const dialog=document.createElement('dialog');dialog.id='columnSettings';dialog.className='columns-dialog';
    dialog.setAttribute('aria-labelledby','columnsTitle');
    dialog.innerHTML=`<form method="dialog" class="columns-shell"><header><h2 id="columnsTitle">列表设置</h2><button type="button" class="columns-close" aria-label="关闭列表设置">×</button></header>
      <div class="columns-controls"><input class="filter-input" id="fieldSearch" aria-label="搜索字段" placeholder="搜索字段">
      <label>参考配置 <select id="fieldPreset" aria-label="参考配置"><option value="">选择参考对象</option><option value="reference">图2默认配置</option><option value="available">仅已有数据</option><option value="compact">常用字段</option></select></label>
      <button type="button" class="demo-button" id="restoreColumns">恢复</button></div>
      <p class="demo-muted columns-hint">拖动顺序手柄调整位置，也可用上下按钮或 Alt＋方向键。点击确定后生效；操作列固定保留。</p>
      <div class="columns-list"><table><thead><tr><th>序号</th><th>字段</th><th>显示列</th><th>冻结列</th><th>列宽</th><th>顺序</th></tr></thead><tbody id="columnRows"></tbody></table></div>
      <div class="columns-error" role="alert"></div><footer><span class="demo-muted" id="columnCount"></span><button type="button" class="demo-button" id="cancelColumns">取消</button><button class="demo-button primary" type="submit">确定</button></footer></form>`;
    document.body.append(dialog);
    let draft=saved();const search=dialog.querySelector('#fieldSearch'),body=dialog.querySelector('#columnRows'),error=dialog.querySelector('.columns-error');
    const draw=()=>{
      const query=search.value.trim().toLowerCase();error.textContent='';
      body.innerHTML=draft.map((c,index)=>{
        const f=boot.demand_fields.find(f=>f.key===c.key),name=label(f);
        if(query&&!(name+f.label+f.note).toLowerCase().includes(query))return '';
        return `<tr data-field="${c.key}"><td>${index+1}</td><td><span class="field-name">${esc(name)}</span><small class="field-source source-${f.source}" title="${esc(f.note)}">${sources[f.source]} · ${esc(f.note)}</small></td>
          <td><input class="column-switch" type="checkbox" role="switch" data-setting="visible" aria-label="显示 ${esc(name)}" ${c.visible?'checked':''}></td>
          <td><input class="column-switch" type="checkbox" role="switch" data-setting="frozen" aria-label="冻结 ${esc(name)}" ${c.frozen?'checked':''}></td>
          <td><input class="column-width" type="number" data-setting="width" aria-label="${esc(name)} 列宽" value="${c.width}" min="60" max="600" step="1" required></td>
          <td><div class="column-order"><button type="button" draggable="true" class="column-drag" aria-label="拖动排序 ${esc(name)}" title="拖动排序；Alt＋上下键也可调整">⠿</button><button type="button" data-move="-1" aria-label="上移 ${esc(name)}" ${index===0?'disabled':''}>↑</button><button type="button" data-move="1" aria-label="下移 ${esc(name)}" ${index===draft.length-1?'disabled':''}>↓</button></div></td></tr>`;
      }).join('');
      if(!body.children.length)body.innerHTML='<tr><td colspan="6">没有匹配字段</td></tr>';
      dialog.querySelector('#columnCount').textContent=`已选 ${draft.filter(c=>c.visible).length} / ${draft.length} 个字段`;
      body.querySelectorAll('[data-field]').forEach(row=>{
        const key=row.dataset.field;
        row.querySelectorAll('[data-setting]').forEach(input=>input.oninput=()=>{
          draft.find(c=>c.key===key)[input.dataset.setting]=input.type==='checkbox'?input.checked:Number(input.value);
          dialog.querySelector('#columnCount').textContent=`已选 ${draft.filter(c=>c.visible).length} / ${draft.length} 个字段`;
        });
        const shift=delta=>{const at=draft.findIndex(c=>c.key===key),target=draft[at+delta];if(target){draft=move(draft,key,target.key,delta>0);draw();body.querySelector(`[data-field="${key}"] .column-drag`)?.focus()}};
        row.querySelectorAll('[data-move]').forEach(b=>b.onclick=()=>shift(Number(b.dataset.move)));
        const handle=row.querySelector('.column-drag');
        handle.onkeydown=e=>{if(e.altKey&&['ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();shift(e.key==='ArrowUp'?-1:1)}};
        handle.ondragstart=e=>{settingsDrag=key;e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',key);row.classList.add('dragging')};
        handle.ondragend=()=>{settingsDrag=null;body.querySelectorAll('.dragging,.drop-before,.drop-after').forEach(el=>el.classList.remove('dragging','drop-before','drop-after'))};
        row.ondragover=e=>{if(!settingsDrag)return;e.preventDefault();e.dataTransfer.dropEffect='move';body.querySelectorAll('.drop-before,.drop-after').forEach(el=>el.classList.remove('drop-before','drop-after'));const r=row.getBoundingClientRect();row.classList.add(e.clientY>r.top+r.height/2?'drop-after':'drop-before')};
        row.ondrop=e=>{if(!settingsDrag)return;e.preventDefault();const r=row.getBoundingClientRect();draft=move(draft,settingsDrag,key,e.clientY>r.top+r.height/2);settingsDrag=null;draw()};
      });
    };
    search.oninput=draw;
    const close=()=>{dialog.close();dialog.remove();settingsDrag=null};
    dialog.querySelector('.columns-close').onclick=close;dialog.querySelector('#cancelColumns').onclick=close;
    dialog.addEventListener('cancel',e=>{e.preventDefault();close()});
    dialog.querySelector('#restoreColumns').onclick=()=>{draft=defaults();search.value='';dialog.querySelector('#fieldPreset').value='reference';draw()};
    dialog.querySelector('#fieldPreset').onchange=e=>{
      if(!e.target.value)return;draft=defaults();
      if(e.target.value!=='reference')draft=draft.map(c=>({...c,visible:e.target.value==='available'?boot.demand_fields.find(f=>f.key===c.key).source!=='missing':compact.has(c.key),frozen:['id','name'].includes(c.key)}));
      search.value='';draw();
    };
    dialog.querySelector('form').onsubmit=e=>{
      e.preventDefault();if(!draft.some(c=>c.visible)){error.textContent='请至少保留一个显示字段。';return}
      if(draft.some(c=>!Number.isInteger(c.width)||c.width<60||c.width>600)){error.textContent='列宽须为60～600的整数像素。';return}
      try{localStorage.setItem(KEY,JSON.stringify(draft))}catch{error.textContent='浏览器无法保存配置，请检查本地存储权限。';return}
      close();onConfirm();
    };
    draw();dialog.showModal();search.focus();
  }
  return {render};
})();
