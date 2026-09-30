from __future__ import annotations

import datetime as dt
import json
import uuid

from ..v2.evaluation import write_gzip
from ..v2.market import TZ
from ..v2.store import ConflictError, dumps
from .data import load_histories
from .forward import advance_daily, checked_daily
from .portfolio import Portfolio, statistics
from .schema import ENGINE_VERSION, Plan, fingerprint

KEY = 'portfolio_observation'


def control(store, action, plan, revision=None, now=None):
    now = now or dt.datetime.now(TZ)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT value FROM meta WHERE key=?', (KEY,)).fetchone()
        study = json.loads(row[0]) if row else None
        if action == 'start':
            if study:
                raise ValueError('请先归档现有观察；不会覆盖已保存的记录')
            if plan.research_interval != 'daily':
                raise ValueError('独立前向对照目前仅支持日线计划；盘中模式仍使用总览的模拟账户')
            study = {'id': uuid.uuid4().hex, 'revision': 1, 'status': 'active',
                     'engine': ENGINE_VERSION, 'created_at': now.isoformat(),
                     'plan': plan.model_dump(), 'plan_key': fingerprint(plan),
                     'books': {}, 'receipts': []}
        else:
            if not study or revision != study['revision']:
                raise ConflictError('观察记录已更新，请刷新后重试')
            if action not in {'pause', 'resume', 'archive'}:
                raise ValueError('观察操作无效')
            if action == 'resume' and study['engine'] != ENGINE_VERSION:
                raise ValueError('引擎版本已变化，请归档旧观察并新建，避免混合不同执行规则')
            study['status'] = {'pause': 'paused', 'resume': 'active', 'archive': 'archived'}[action]
            study['revision'] += 1
            study.setdefault('events', []).append({'action': action, 'at': now.isoformat()})
            if action == 'archive':
                c.execute('INSERT INTO meta VALUES(?,?)', (f'{KEY}:{study["id"]}', dumps(study)))
                study = None
        c.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  (KEY, dumps(study)))
    return study


def update_observation(store, study_id, progress, histories=None, now=None):
    now = now or dt.datetime.now(TZ)
    study = store.meta(KEY)
    if not study or study['id'] != study_id or study['status'] != 'active':
        return {'note': '观察已暂停或归档，未产生新成交'}
    if study['engine'] != ENGINE_VERSION:
        raise ValueError('引擎已升级，请归档后新建观察；旧记录保持不变')
    plan = Plan.model_validate(study['plan'])
    end = now.date() if now.hour >= 16 else now.date() - dt.timedelta(days=1)
    if histories is None:
        histories, _ = load_histories(store, plan, end - dt.timedelta(days=30), end, progress)
    histories, checked, latest = checked_daily(histories, plan, now)
    if study.get('last_quote') and str(latest.date()) <= study['last_quote']:
        return {'note': '没有新行情，未重复成交', 'last_quote': study['last_quote']}
    revision = study['revision']
    first = not study['books']
    initial = plan.starting_cash
    if first:
        study['anchor_date'] = str(now.date())
        study['anchor_quote'] = str(latest.date())
    variants = {'strategy': (plan, plan.method),
                'unlevered': (plan.model_copy(update={'research_multiple': 1}), plan.method),
                'hold': (plan.model_copy(update={'research_multiple': 1}), 'hold')}
    for index, (key, (variant, method)) in enumerate(variants.items()):
        progress(50 + index * 15, f'前向对照：{key}')
        engine = Portfolio(store, variant, histories, method=method,
                           restored=study['books'].get(key), progress=progress)
        advance_daily(engine, latest, now)
        study['books'][key] = engine.state
    # Receipt snapshots make revised data inspectable without silently recalculating old equity.
    receipt_id = uuid.uuid4().hex
    snapshot = f'observations/{study_id}/{receipt_id}.json.gz'
    write_gzip(store.root / snapshot, {s: f.to_json(orient='split', date_format='iso', double_precision=15)
                                      for s, f in histories.items()})
    study['receipts'].append({'at': now.isoformat(), 'quote_date': str(latest.date()),
                              'snapshot': snapshot, 'audit': checked})
    study.update(last_quote=str(latest.date()), updated_at=now.isoformat(), revision=revision + 1)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current = json.loads(c.execute('SELECT value FROM meta WHERE key=?', (KEY,)).fetchone()[0])
        if not current or current['id'] != study_id or current['revision'] != revision or current['status'] != 'active':
            raise ConflictError('观察状态已更改，本次结果未写入；不会覆盖暂停或归档操作')
        c.execute('UPDATE meta SET value=? WHERE key=?', (dumps(study), KEY))
    return {'study_id': study_id, 'last_quote': study['last_quote'],
            'note': '首次只登记未来指令，没有回填历史收益' if first else '已保存三组独立模拟对照',
            'starting_cash': initial}


def describe(study, current_plan, include_curve=True):
    plan = Plan.model_validate(study['plan'])
    anchor = study.get('anchor_date', study['created_at'][:10])
    books = {}
    for key, state in study['books'].items():
        points = [r for r in state['curve'] if r['date'] > anchor]
        # Count only actual receipt days; prices reconstructed after downtime are separate.
        received = {r['quote_date'] for r in study['receipts']
                    if r['quote_date'] > anchor and r['quote_date'] == r['at'][:10]}
        curve = [{'date': anchor, 'equity': plan.starting_cash}] + points
        metrics = statistics({**state, 'curve': points}, plan.starting_cash)
        # A handful of observations must not be advertised as an annualized track record.
        metrics.pop('annualized_return', None)
        metrics.pop('sharpe', None)
        books[key] = {**metrics, 'sessions': len(points),
                      'observed_sessions': len({r['date'] for r in points} & received),
                      'reconstructed_sessions': len({r['date'] for r in points} - received)}
        if include_curve:
            books[key]['curve'] = curve
    with_plan = fingerprint(current_plan) == study['plan_key']
    return {k: study.get(k) for k in ['id', 'revision', 'status', 'engine', 'created_at', 'updated_at',
                                     'anchor_date', 'last_quote', 'plan', 'plan_key']} | {
        'same_plan': with_plan, 'engine_matches': study['engine'] == ENGINE_VERSION,
        'books': books, 'receipt_count': len(study['receipts']),
        'verdict': '等待未来行情' if not books or not books['strategy']['sessions'] else '观察中，尚不能认定收益稳定',
    }


def observation_view(store, current_plan):
    study = store.meta(KEY)
    with store.connect() as c:
        rows = c.execute("SELECT value FROM meta WHERE key LIKE 'portfolio_observation:%' ORDER BY key").fetchall()
        latest = c.execute("SELECT id,status,message,error,updated FROM jobs WHERE kind='portfolio_observe' "
                           "AND json_extract(request,'$.observation_id')=? ORDER BY created DESC LIMIT 1",
                           (study['id'] if study else '',)).fetchone()
    archives = sorted((json.loads(row[0]) for row in rows), key=lambda s: s['created_at'], reverse=True)
    return {'current': describe(study, current_plan) if study else None,
            'archives': [describe(item, current_plan, include_curve=False) for item in archives],
            'job': dict(latest) if latest else None}


def enqueue_observation(store, now=None, *, manual=False):
    from ..v2.schema import JobRequest

    now = now or dt.datetime.now(TZ)
    study = store.meta(KEY)
    if not study or study['status'] != 'active' or study['engine'] != ENGINE_VERSION:
        return None
    if not manual and (now.weekday() >= 5 or now.hour < 16):
        return None
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current = json.loads(c.execute('SELECT value FROM meta WHERE key=?', (KEY,)).fetchone()[0])
        if not current or current['id'] != study['id'] or current['status'] != 'active':
            return None
        if c.execute("SELECT 1 FROM jobs WHERE kind='portfolio_observe' AND status IN ('running','queued') "
                     "AND json_extract(request,'$.observation_id')=?", (study['id'],)).fetchone():
            return None
        attempts = c.execute("SELECT status,updated FROM jobs WHERE kind='portfolio_observe' "
                             "AND json_extract(request,'$.observation_id')=? "
                             "AND json_extract(request,'$.observation_automatic')=1 AND created>=? ORDER BY created DESC",
                             (study['id'], now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())).fetchall()
        if not manual:
            if current.get('last_quote') == str(now.date()):
                return None
            if len(attempts) >= 3 or attempts and now.timestamp() - attempts[0]['updated'] < 900:
                return None
        request = JobRequest(kind='portfolio_observe', observation_id=study['id'],
                             observation_automatic=not manual,
                             investment=Plan.model_validate(study['plan']), compare_models=False)
        job_id = uuid.uuid4().hex
        c.execute("INSERT INTO jobs(id,kind,status,message,request,created,updated) "
                  "VALUES(?,'portfolio_observe','queued','等待前向观察',?,?,?)",
                  (job_id, dumps(request.model_dump()), now.timestamp(), now.timestamp()))
    return store.job(job_id)
