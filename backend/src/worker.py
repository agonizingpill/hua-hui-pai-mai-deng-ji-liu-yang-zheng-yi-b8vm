import os,time,json,logging
from pathlib import Path
import redis
from .db import Session
from .models import VaseSample,Consignment
from .common import lock
from .grading import predict
q=redis.Redis.from_url(os.environ['REDIS_URL'],decode_responses=True)
while True:
    Path('/tmp/floral-heartbeat').touch()
    try:
        item=q.blpop('floral-grade',timeout=2)
        if not item:continue
        sid,_,epoch=item[1].partition(':')
        with Session()as s:
            sample=lock(s,VaseSample,int(sid))
            if sample.state!='queued'or str(sample.estimate_epoch)!=epoch:continue
            sample.state='running';s.commit()
            try:result=predict(sample,s.get(Consignment,sample.consignment_id))
            except Exception as ex:
                logging.exception('grade failed');fresh=lock(s,VaseSample,int(sid))
                if fresh.state=='running'and str(fresh.estimate_epoch)==epoch:fresh.state='failed';fresh.error=str(ex)[:450];s.commit()
                continue
            fresh=lock(s,VaseSample,int(sid))
            if fresh.state!='running'or str(fresh.estimate_epoch)!=epoch:continue
            fresh.prediction=result;fresh.state='ready';fresh.error=None;Path(f'/artifacts/grade-{fresh.id}.json').write_text(json.dumps(result,ensure_ascii=False));s.commit()
    except Exception:logging.exception('queue unavailable');time.sleep(1)
