from uuid import uuid4
from fastapi import HTTPException
from .models import Consignment,VaseSample,SwapReview
from .common import lock,pack,audit
from .grading import predict
def open_review(s,d,u):
    if d.sample_a_id==d.sample_b_id:raise HTTPException(409,'两枝留样须为不同留样签')
    a=lock(s,VaseSample,d.sample_a_id);b=lock(s,VaseSample,d.sample_b_id)
    ca=s.get(Consignment,a.consignment_id);cb=s.get(Consignment,b.consignment_id)
    if ca.id==cb.id:raise HTTPException(409,'两枝留样同属一个到场批次，无需对调')
    if ca.variety!=cb.variety:raise HTTPException(409,'仅同品种的两批留样可发起对调复核')
    if a.observed_days is None or b.observed_days is None:raise HTTPException(409,'两枝留样均须先封存实际瓶插终点')
    estimates={'a_original':predict(a,ca),'a_swapped':predict(a,cb),'b_original':predict(b,cb),'b_swapped':predict(b,ca),'a_observed_days':a.observed_days,'b_observed_days':b.observed_days}
    closer=lambda key:abs(estimates[key+'_swapped']['vase_days']-estimates[key+'_observed_days'])<abs(estimates[key+'_original']['vase_days']-estimates[key+'_observed_days'])
    verdict='suggested' if closer('a')and closer('b')else 'not_suggested'
    x=SwapReview(code='SWAP-'+uuid4().hex[:16],sample_a_id=a.id,sample_b_id=b.id,consignment_a_id=ca.id,consignment_b_id=cb.id,estimates=estimates,verdict=verdict,note=d.note)
    s.add(x);s.flush();x.code=f'SWAP-{x.id:04d}'
    audit(s,u,'发起留样对调复核 '+x.code+' '+ca.code+'↔'+cb.code);return pack(x)
def confirm(s,id,u):
    case=lock(s,SwapReview,id)
    if case.state=='confirmed':return pack(case)
    if case.state!='open':raise HTTPException(409,'该对调复核已整单退回，不可再确认')
    if case.verdict!='suggested':raise HTTPException(409,'模型复核未建议对调，不能确认换批')
    first,second=sorted((case.sample_a_id,case.sample_b_id));lock(s,VaseSample,first);lock(s,VaseSample,second)
    a=s.get(VaseSample,case.sample_a_id);b=s.get(VaseSample,case.sample_b_id)
    problem='任一枝留样已核级'if a.state=='approved'or b.state=='approved'else'任一枝留样已另案换批'if a.consignment_id!=case.consignment_a_id or b.consignment_id!=case.consignment_b_id else None
    if problem:
        case.state='returned';audit(s,u,'对调复核整单退回 '+case.code+'：'+problem);s.commit()
        raise HTTPException(409,problem+'，对调复核整单退回')
    case.archive={'a':{'prediction':a.prediction,'state':a.state},'b':{'prediction':b.prediction,'state':b.state}}
    a.consignment_id,b.consignment_id=case.consignment_b_id,case.consignment_a_id
    a.prediction,b.prediction=case.estimates['a_swapped'],case.estimates['b_swapped']
    for x in (a,b):x.state='ready';x.error=None;x.estimate_epoch+=1
    case.state='confirmed';audit(s,u,'主管确认对调换批 '+case.code);return pack(case)
