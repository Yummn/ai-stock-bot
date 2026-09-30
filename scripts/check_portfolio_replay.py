"""Replay a frozen investment dataset with a fresh model cache and compare."""
import argparse
import json
import os
import time
from io import StringIO
from pathlib import Path

for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):
    os.environ.setdefault(key,'2')

import pandas as pd
from ai_stock_bot.v2.evaluation import read_gzip
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.schema import Plan
from ai_stock_bot.v4.portfolio import simulate


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('job')
    parser.add_argument('--method', choices=('ml','ml_class','cnn','cnn_class'), default='ml')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]/'data'/'v4'
    original=Store(root).job(args.job,True)['result']
    frozen=read_gzip(root/'portfolio_tests'/args.job/'market.json.gz')
    histories={s:pd.read_json(StringIO(value),orient='split') for s,value in frozen['frames'].items()}
    plan=Plan.model_validate(original['plan'])
    destination=root/'replay_checks'/str(int(time.time()))
    store=Store(destination)
    receipt={'source_job':args.job,'method':args.method,'checks':[]}
    for repetition in range(2):
        started=time.monotonic()
        result=simulate(store,plan,histories,original['start'],original['end'],args.method)
        reference=original['results'][args.method]
        assert result['curve']==reference['curve'], 'Replay equity differs'
        assert result['trades']==reference['trades'], 'Replay trades differ'
        receipt['checks'].append({'mode':'cold' if repetition==0 else 'cached','seconds':round(time.monotonic()-started,2),'months':len(result['training']),'matched':True})
        print(json.dumps(receipt['checks'][-1]),flush=True)
    (destination/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(destination,flush=True)
