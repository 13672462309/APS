'use strict';
window.ProductionViews = (() => {
  function renderEditor(root){
    const revision=Boolean(session.selected_job_id);
    root.innerHTML=`<div class="demo-card"><h2>${revision?'修改产量 · 新版本草稿':'需求确认 · 成品分解数量'}</h2>
      <p class="demo-muted">只填写成品分解数量，计划产量自动汇总。当前每物料一条分解行，两者相等；数量包含固定在制。人工填写的物料在循环中锁定，未填写的物料由后续排产生成。保存不会启动求解。</p>
      <input class="filter-input" id="editFilter" placeholder="筛选物料编号或名称" aria-label="筛选分解行">
      <form id="quantityForm"><div class="demo-table-wrap"><table class="demo-table"><thead><tr><th>物料编号</th><th>物料</th><th>固定在制(吨)</th><th>成品分解数量(吨)</th><th>计划产量(吨)</th><th>新增量选项范围</th><th>锁定状态</th></tr></thead><tbody>
      ${session.products.map(d=>{const q=d.fields.decomposed_quantity;return `<tr data-search="${esc(d.id+d.name)}"><td>${esc(d.id)}</td><td class="wrap">${esc(d.name)}</td><td>${fmt(d.wip)}</td><td><input class="qty-input" type="number" min="${d.wip}" step="any" data-pid="${d.id}" data-original="${q??''}" value="${q??''}" placeholder="待填写" aria-label="${d.id} 成品分解数量"></td><td data-plan-total>${fmt(q)}</td><td>${d.options[0]}～${d.options.at(-1)}<br><small>按实际整批选项校验</small></td><td>${Object.hasOwn(session.constraints.quantities,d.id)?button('已锁定 · 解除','unlock',{product:d.id}):'未锁定'}</td></tr>`}).join('')}
      </tbody></table></div><div class="demo-actions"><button class="demo-button primary" type="submit">保存成品分解数量</button><button class="demo-button" type="button" id="backResult">${revision?'返回方案':'返回需求确认'}</button></div></form></div>`;
    root.querySelector('#editFilter').oninput=e=>root.querySelectorAll('[data-search]').forEach(row=>row.hidden=!row.dataset.search.includes(e.target.value.trim()));
    root.querySelectorAll('[data-pid]').forEach(input=>input.oninput=()=>{input.closest('tr').querySelector('[data-plan-total]').textContent=fmt(input.value===''?null:Number(input.value))});
    root.querySelector('#backResult').onclick=()=>send('',revision?{type:'stage',phase:5}:{type:'query',all:true});
    root.querySelector('#quantityForm').onsubmit=e=>{
      e.preventDefault();const changes=[];
      for(const input of root.querySelectorAll('[data-pid]')){
        if(input.value===''&&input.dataset.original==='')continue;
        if(input.value===''){toast('已填写数量不能直接清空；如需交给 Agent 优化，请解除锁定。');return}
        if(input.dataset.original===''||Math.abs(Number(input.value)-Number(input.dataset.original))>1e-6)
          changes.push({type:'quantity',product:input.dataset.pid,quantity:Number(input.value),kind:'total'});
      }
      if(!changes.length){toast('请先填写或修改至少一条成品分解数量。');return}
      send('',{type:'edit',changes});
    };
    bind(root);
  }

  function renderOrders(body,result){
    const rows=result.order_details;
    if(!rows){body.innerHTML='<div class="demo-note">正在读取该方案的冻结数据，暂无法展示订单明细。</div>';return}
    body.innerHTML=`${active()?'<p class="demo-muted">正在循环优化，以下为当前所选轮次的排程结果。</p>':''}${session.dirty&&!active()?'<p class="demo-muted">存在未重算的修改；以下保留已求解版本的明细。</p>':''}
      <div class="card order-details-card"><div class="card-title">订单排产明细（共 ${rows.length} 条）</div>
      <div class="order-details-scroll"><table class="tb order-details-table"><thead><tr><th>订单号</th><th>物料</th><th>分类</th><th>产线</th><th>数量</th><th>交付</th><th>状态</th></tr></thead><tbody>
      ${rows.map(row=>`<tr><td><b>${esc(row.order_number??'—')}</b></td><td class="order-material" title="${esc(row.material)}">${esc(row.material)}<small>${esc(row.product_id)}</small></td><td>${esc(row.category)}</td><td>${esc(row.lines.join(' / ')||'—')}</td><td class="num">${fmt(row.quantity,2)} t</td><td>${esc(row.delivery??'—')}</td><td><span class="tag ${row.status==='已排入'?'tag-ok':'tag-warn'}">${esc(row.status)}</span></td></tr>`).join('')||'<tr><td colspan="7">暂无计划明细</td></tr>'}
      </tbody></table></div><p class="order-source-note">当前按物料汇总展示；尚无客户订单号及逐订单交期，显示“—”。“已排入”表示进入当前排程，不代表已下达或已生产。</p></div>`;
  }
  return {renderEditor,renderOrders};
})();
