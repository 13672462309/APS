"""Present the prototype's order columns without inventing customer orders."""


def order_details_for(data, result):
    evaluation=result['evaluation']
    grouped={}
    for row in evaluation.get('rows',[]):
        grouped.setdefault(row['product_id'],[]).append(row)
    details=[]
    for pid, plan in result['plan'].items():
        demand=data['demands'][pid]
        quantity=round(plan['quantity']+demand['wip'],4)
        if quantity<=0:continue
        rows=grouped.get(pid,[])
        scheduled=sum(row['quantity'] for row in rows)
        if not rows:status='未排入'
        elif any(not row.get('valid',False) for row in rows) or scheduled>quantity+1e-6:status='待检查'
        elif scheduled<quantity-1e-6:status='部分排入'
        else:status='已排入'
        details.append(dict(product_id=pid,order_number=None,material=demand['name'],
            category=demand['category'],lines=list(dict.fromkeys(row['line_name'] for row in rows)),
            quantity=quantity,delivery=None,status=status,
            decomposition_rows=[dict(line=row['line_name'],quantity=row['quantity'],
                                     start=row['start'],end=row['end']) for row in rows]))
    return details
