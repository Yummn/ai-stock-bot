import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ai_stock_bot.v2.api import create_app
from ai_stock_bot.v2.execution import Account
from ai_stock_bot.v2.schema import AppConfig, JobRequest, Risk
from ai_stock_bot.v2.store import Store, ConflictError
from ai_stock_bot.v2.evaluation import write_gzip
from ai_stock_bot.v4.data import audit, PortfolioMarket
from ai_stock_bot.v4.data import EXECUTION_INTERVAL
from ai_stock_bot.v2.market import Market
from ai_stock_bot.v4.forecast import samples, Forecasts
from ai_stock_bot.v4.leverage import daily_leverage_scenarios
from ai_stock_bot.v4.intraday import research as intraday_research
from ai_stock_bot.v4.hourly_paper import update_hourly_portfolio
from ai_stock_bot.v4.portfolio import Portfolio, capped_weights, hold_weights, simulate
from ai_stock_bot.v4.schema import ASSETS, ENGINE_VERSION, PROFILE_PRESETS, Plan, comparison_fingerprint, fingerprint, with_profile
from ai_stock_bot.v4.service import comparison, forward_progress, frequency_comparison, report_for, save_settings, settings, snapshot_revisions, update_forward_baseline, update_portfolio, summary
from ai_stock_bot.v4.schedule import enqueue_daily


@pytest.fixture
def histories():
    dates = pd.bdate_range('2020-01-01', '2025-12-31')
    result = {}
    for i, symbol in enumerate(['510300.SH', '510500.SH', '518880.SH']):
        rng = np.random.default_rng(80+i)
        close = 3*np.exp(np.cumsum(rng.normal(.0007, .005, len(dates))))
        opening = np.r_[close[0], close[:-1]]
        result[symbol] = pd.DataFrame({'open':opening,'high':np.maximum(close,opening)*1.003,'low':np.minimum(close,opening)*.997,'close':close,'adjusted':close,'volume':1e8,'source':'fixture'}, index=dates)
    return result


@pytest.fixture
def plan(histories):
    return Plan(symbols=list(histories), model_iterations=20, min_samples=100)


def test_minimum_commission_respects_cash():
    account = Account(Risk(starting_cash=1000, max_trade_cash=1000, max_position_pct=1, min_commission=5, commission_rate=0, slippage_bps=0))
    trade, _ = account.trade('510300.SH','buy',1,'2025-01-02','test','test',quantity=1000)
    assert trade['quantity']==900 and trade['fee']==5 and account.cash==95


def test_cap_and_asset_group_selection(tmp_path, histories, plan):
    rows = [{'symbol':'a','volatility':.001},{'symbol':'b','volatility':.8},{'symbol':'c','volatility':.2}]
    weights = capped_weights(rows,.7,.35)
    assert max(weights.values())<=.35
    assert sum(weights.values())==pytest.approx(.7)
    engine=Portfolio(Store(tmp_path),plan,histories)
    target=engine.make_plan(pd.Timestamp('2024-12-31'))
    assert not {'510300.SH','510500.SH'}.issubset(target['weights'])
    assert sum(target['weights'].values())<=plan.max_exposure+1e-10


def test_focused_validation_runs_only_selected_model_and_risk_controls(tmp_path, histories, plan, monkeypatch):
    from ai_stock_bot.v4 import service
    store = Store(tmp_path)
    checked = audit(histories, plan.symbols)
    monkeypatch.setattr(service, 'load_histories', lambda *args, **kwargs: (histories, checked))
    selected = plan.model_copy(update={'research_multiple': 2, 'trend_signal': 'multi_horizon',
                                       'exposure_policy': 'volatility_target'})
    request = JobRequest(kind='portfolio_test', investment=selected, years=[2024], compare_models=False)
    job = store.create_job(request.model_dump())
    result = comparison(store, job['id'], request, lambda *args: None)
    assert result['complete'] and result['scope'] == 'current_plan'
    assert set(result['results']) == {'rotation', 'hold', 'hold_full', 'stress', 'unlevered', 'hold_1x'}
    assert result['results']['unlevered']['interest_paid'] == 0
    assert not result['results']['rotation']['training']
    store.update_job(job['id'], status='done', result=result)
    assert report_for(store, selected)['id'] == job['id']
    assert report_for(store, selected.model_copy(update={'target_volatility': .20})) is None


@pytest.mark.parametrize('profile', ['conservative', 'balanced', 'aggressive'])
def test_profile_presets_keep_strategy_independent(profile):
    starting = Plan(method='ml_class', rebalance='monthly')
    candidate = with_profile(starting, profile)
    assert candidate.method == 'ml_class' and candidate.rebalance == 'monthly'
    assert candidate.profile == profile
    assert candidate.allocation == PROFILE_PRESETS[profile]['allocation']
    assert sum(candidate.allocation.values()) == pytest.approx(1.)
    changed = Plan.model_validate({**candidate.model_dump(), 'max_exposure': candidate.max_exposure - .01})
    assert changed.profile == 'custom'


def test_sector_preferences_reallocate_and_match_hold_benchmark(tmp_path, histories, plan):
    custom = Plan.model_validate({**plan.model_dump(), 'profile': 'custom',
                                  'max_weight': .6,
                                  'allocation': {'中国股票': .20, '海外股票': .10, '黄金': .30, '国债': .40}})
    engine = Portfolio(Store(tmp_path), custom, histories)
    date = pd.Timestamp('2024-12-31')
    for signal in engine.signals.values():
        signal.loc[date, ['m60', 'm120', 'above', 'volatility']] = [.1, .1, .1, .1]
    decision = engine.make_plan(date)
    assert len(decision['weights']) == 2
    weights_by_group = {ASSETS[symbol]['group']: weight for symbol, weight in decision['weights'].items()}
    assert weights_by_group['中国股票'] == pytest.approx(custom.max_exposure * .20 / .50)
    assert weights_by_group['黄金'] == pytest.approx(custom.max_exposure * .30 / .50)
    baseline = hold_weights(custom)
    assert sum(baseline.values()) == pytest.approx(custom.max_exposure)
    assert sum(decision['weights'].values()) == pytest.approx(sum(baseline.values()))
    with pytest.raises(ValueError, match='100%'):
        Plan.model_validate({**custom.model_dump(), 'allocation': {'中国股票': .2}})


def test_forward_benchmark_starts_at_saved_plan_not_old_history(tmp_path, histories, plan):
    store = Store(tmp_path)
    engine = Portfolio(store, plan, histories)
    anchor = pd.Timestamp('2024-01-02')
    engine.step(anchor, execute=False)
    engine.state['plan_key'] = fingerprint(plan)
    now = dt.datetime(2024, 1, 2, 17)
    update_forward_baseline(engine, store, plan, histories, now, anchor, reset=True)
    first = forward_progress(engine.state, plan)
    assert first['status'] == 'collecting' and first['sessions'] == 0
    assert first['strategy_return'] == first['hold_return'] == 0
    next_date = pd.Timestamp('2024-01-03')
    engine.step(next_date)
    update_forward_baseline(engine, store, plan, histories, now, next_date, reset=False)
    later = forward_progress(engine.state, plan)
    assert later['sessions'] == later['observed_updates'] == 1
    assert np.isfinite(later['strategy_return']) and np.isfinite(later['hold_return'])
    changed = with_profile(plan, 'aggressive')
    assert forward_progress(engine.state, changed)['status'] == 'not_started'


def test_snapshot_revision_warning_compares_same_period_and_symbols(tmp_path, plan):
    store = Store(tmp_path)
    job = store.create_job(JobRequest(kind='portfolio_test', investment=plan, config=AppConfig()).model_dump())
    store.update_job(job['id'], status='done', result={
        'start': '2021-01-01', 'end': '2025-12-31',
        'audit': {'rows': [{'symbol': '510300.SH', 'hash': 'old'}]}})
    current = {'id': 'new', 'start': '2021-01-01', 'end': '2025-12-31',
               'audit': {'rows': [{'symbol': '510300.SH', 'hash': 'new'}]}}
    warning = snapshot_revisions(store, current)
    assert warning['symbols'] == ['510300.SH']
    assert warning['previous_report_id'] == job['id']
    assert snapshot_revisions(store, {**current, 'end': '2024-12-31'}) is None


def test_all_executions_follow_signal_date(tmp_path,histories,plan):
    result=simulate(Store(tmp_path),plan,histories,'2023-01-01','2024-12-31')
    assert result['trade_count']>0
    assert all(t['signal_date']<t['date'] for t in result['trades'])
    assert all(t['fee']>=5 for t in result['trades'])
    assert all(p['quantity']>=0 for p in result['positions'].values())
    assert all(p['cash']>=-1e-8 for p in result['curve'])


def test_training_labels_causal_and_timestamp_units(histories,plan):
    cutoff=pd.Timestamp('2024-06-01')
    expected=samples(histories,plan,cutoff)
    altered={s:f.copy() for s,f in histories.items()}
    for frame in altered.values():
        frame.loc[frame.index>=cutoff,['open','close','adjusted']]*=100
        frame.index=frame.index.as_unit('us')
    actual=samples(altered,plan,cutoff)
    for a,b in zip(expected,actual):
        np.testing.assert_array_equal(a,b)
    assert pd.Timestamp(actual[-1].max())<cutoff
    assert pd.Timestamp(actual[-1].min()).year>=2021


def test_cnn_sequences_and_labels_use_only_mature_history(histories, plan):
    cutoff = pd.Timestamp('2024-06-01')
    cnn_plan = plan.model_copy(update={'cnn_window': 10})
    expected = samples(histories, cnn_plan, cutoff, cnn_plan.cnn_window)
    altered = {symbol: frame.copy() for symbol, frame in histories.items()}
    for frame in altered.values():
        frame.loc[frame.index >= cutoff, ['open', 'close', 'adjusted']] *= 100
    actual = samples(altered, cnn_plan, cutoff, cnn_plan.cnn_window)
    assert actual[0].shape[1:] == (10, 11)
    for left, right in zip(expected, actual):
        np.testing.assert_array_equal(left, right)
    assert pd.Timestamp(actual[-1].max()) < cutoff


@pytest.mark.parametrize('window', [1, 10])
def test_old_binary_signal_uses_mature_close_returns(histories, plan, window):
    cutoff = pd.Timestamp('2024-06-01')
    symbol = '510300.SH'
    one = {symbol: histories[symbol]}
    x, y, dates, ends = samples(one, plan, cutoff, window, classification=True)
    assert len(x) == len(y) == len(dates) == len(ends) and len(y) > 100
    assert set(np.unique(y)) <= {0., 1.}
    assert pd.Timestamp(ends.max()) < cutoff
    frame = histories[symbol]
    for index in [0, len(y) // 2, -1]:
        signal = frame.index.get_loc(pd.Timestamp(dates[index]))
        expected = float(frame.adjusted.iloc[signal + plan.horizon] / frame.adjusted.iloc[signal] - 1 > .001)
        assert y[index] == expected
    changed = {symbol: frame.copy()}
    changed[symbol].loc[changed[symbol].index >= cutoff, 'adjusted'] *= 20
    for left, right in zip((x, y, dates, ends), samples(changed, plan, cutoff, window, classification=True)):
        np.testing.assert_array_equal(left, right)


@pytest.mark.parametrize('method', ['ml_class', 'cnn_class'])
def test_classification_models_fit_and_reuse_cache(tmp_path, histories, plan, monkeypatch, method):
    monkeypatch.setenv('STOCKLAB_CNN_DEVICE', 'cpu')
    p = plan.model_copy(update={'method': method, 'cnn_window': 10, 'cnn_epochs': 4})
    first = Forecasts(Store(tmp_path), histories, p, model=method)
    first.fit(pd.Timestamp('2024-06-01'))
    assert first.report['target'].endswith('的概率')
    assert first.report['validation_metric'] == 'Brier'
    assert first.report['label_end'] < first.report['cutoff']
    repeat = Forecasts(Store(tmp_path), histories, p, model=method)
    repeat.fit(pd.Timestamp('2024-06-01'))
    assert repeat.report['cached']
    assert repeat.report['status'] == first.report['status']
    if repeat.report['status'] == 'model':
        values, _ = repeat.at(pd.Timestamp('2024-06-03'))
        assert all(0 <= value <= 1 for value in values.values())


def test_classifier_threshold_is_a_signal_filter_not_a_new_execution_rule(tmp_path, histories, plan):
    p = plan.model_copy(update={'method': 'ml_class', 'classification_threshold': .6})
    engine = Portfolio(Store(tmp_path), p, histories)
    date = pd.Timestamp('2024-12-31')
    for signal in engine.signals.values():
        signal.loc[date, ['m60', 'm120', 'above', 'volatility']] = [.1, .1, .1, .1]
    engine.forecasts.at = lambda _: ({'510300.SH': .59, '510500.SH': .9, '518880.SH': .8}, {'status': 'model'})
    decision = engine.make_plan(date)
    assert not decision['weights'].get('510300.SH')
    assert sum(decision['weights'].values()) <= p.max_exposure
    assert next(row for row in decision['rows'] if row['symbol'] == '510300.SH')['reason'] == '上涨概率未达门槛'


def test_cnn_fits_and_reuses_the_same_verified_month(tmp_path, histories, plan, monkeypatch):
    monkeypatch.setenv('STOCKLAB_CNN_DEVICE', 'cpu')
    cnn_plan = plan.model_copy(update={'method': 'cnn', 'cnn_window': 10, 'cnn_epochs': 4})
    first = Forecasts(Store(tmp_path), histories, cnn_plan, model='cnn')
    first.fit(pd.Timestamp('2024-06-01'))
    assert first.report['device'] == 'cpu'
    assert first.report['validation_samples'] >= 50
    assert first.report['label_end'] < first.report['cutoff']
    repeat = Forecasts(Store(tmp_path), histories, cnn_plan, model='cnn')
    repeat.fit(pd.Timestamp('2024-06-01'))
    assert repeat.report['cached']
    assert repeat.report['status'] == first.report['status']
    assert repeat.report['validation_mse'] == first.report['validation_mse']


def test_cnn_portfolio_uses_next_open_and_same_risk_limits(tmp_path, histories, plan, monkeypatch):
    monkeypatch.setenv('STOCKLAB_CNN_DEVICE', 'cpu')
    cnn_plan = plan.model_copy(update={'method': 'cnn', 'cnn_window': 10, 'cnn_epochs': 4})
    result = simulate(Store(tmp_path), cnn_plan, histories, '2023-01-01', '2023-03-31')
    assert result['training'] and all(row['model'] == 'cnn' for row in result['training'])
    assert all(row['label_end'] is None or row['label_end'] < row['cutoff'] for row in result['training'])
    assert all(trade['signal_date'] < trade['date'] for trade in result['trades'])
    assert all(sum(decision['weights'].values()) <= cnn_plan.max_exposure + 1e-8 for decision in result['decisions'])


@pytest.mark.parametrize('method', ['ml_class', 'cnn_class'])
def test_classification_portfolio_uses_same_next_open_and_costs(tmp_path, histories, plan, monkeypatch, method):
    monkeypatch.setenv('STOCKLAB_CNN_DEVICE', 'cpu')
    p = plan.model_copy(update={'method': method, 'cnn_window': 10, 'cnn_epochs': 4})
    result = simulate(Store(tmp_path), p, histories, '2023-01-01', '2023-03-31')
    assert result['training'] and all(row['model'] == method for row in result['training'])
    assert all(row['label_end'] is None or row['label_end'] < row['cutoff'] for row in result['training'])
    assert all(trade['signal_date'] < trade['date'] and trade['fee'] >= p.min_commission for trade in result['trades'])
    assert all(sum(decision['weights'].values()) <= p.max_exposure + 1e-8 for decision in result['decisions'])


def test_cnn_forward_update_creates_a_future_plan_without_past_orders(tmp_path, histories, plan, monkeypatch):
    import ai_stock_bot.v4.service as service

    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 12, 31, 17, tzinfo=tz)

    monkeypatch.setattr(service.dt, 'datetime', Clock)
    monkeypatch.setenv('STOCKLAB_CNN_DEVICE', 'cpu')
    cnn_plan = plan.model_copy(update={'method': 'cnn', 'cnn_window': 10, 'cnn_epochs': 4})
    store = Store(tmp_path)
    save_settings(store, cnn_plan, 1)
    update_portfolio(store, cnn_plan, lambda *args: None, histories)
    state = store.meta('portfolio_state')
    assert state['pending']['model']['model'] == 'cnn'
    assert state['pending']['execute_after'] == '2025-12-31'
    assert state['account']['trades'] == []


def test_forecast_validation_purged_and_cache_matches(tmp_path,histories,plan):
    engine=Forecasts(Store(tmp_path),histories,plan)
    engine.fit(pd.Timestamp('2024-06-03'))
    first=engine.report
    assert first['label_end']<first['cutoff']
    assert first['train_samples']>0 and first['validation_samples']>=50
    again=Forecasts(Store(tmp_path),histories,plan)
    again.fit(pd.Timestamp('2024-06-03'))
    assert again.report['cached']
    assert first['validation_mse']==again.report['validation_mse']


def test_monthly_cutoff_identical_after_forward_restart(tmp_path,histories,plan):
    backtest=Forecasts(Store(tmp_path),histories,plan)
    backtest.at(pd.Timestamp('2024-06-03'))
    expected,_=backtest.at(pd.Timestamp('2024-06-17'))
    forward=Forecasts(Store(tmp_path),{s:f.loc[:'2024-06-17'] for s,f in histories.items()},plan)
    actual,report=forward.at(pd.Timestamp('2024-06-17'))
    assert report['cutoff']=='2024-06-01'
    assert actual==expected


def test_damaged_cache_is_rebuilt(tmp_path,histories,plan):
    engine=Forecasts(Store(tmp_path),histories,plan)
    engine.fit(pd.Timestamp('2024-06-03'))
    cache=next((tmp_path/'portfolio_models').glob('*.npz'))
    cache.write_bytes(b'incomplete')
    engine.fit(pd.Timestamp('2024-06-03'))
    assert not engine.report['cached']


def test_audit_rejects_inconsistent_or_missing_bars(histories,plan):
    assert audit(histories,plan.symbols,'2023-01-01','2024-12-31')['ok']
    bad={s:f.copy() for s,f in histories.items()}
    bad['510300.SH']=bad['510300.SH'].drop(pd.Timestamp('2023-03-01'))
    assert not audit(bad,plan.symbols,'2023-01-01','2024-12-31')['ok']
    bad['510300.SH'].iloc[0,bad['510300.SH'].columns.get_loc('high')]=0
    assert not audit(bad,plan.symbols)['ok']


def test_verified_split_preserves_value_and_cost_basis(tmp_path):
    index=pd.to_datetime(['2022-01-12','2022-01-14'])
    frame=pd.DataFrame({'open':[5.,1.], 'close':[5.,1.], 'high':[5.,1.], 'low':[5.,1.], 'adjusted':[1.,1.], 'volume':[1e8,1e8], 'source':'AKShare Tencent OHLC / adjusted'}, index=index)
    p=Plan(symbols=['513100.SH','518880.SH'])
    engine=Portfolio(Store(tmp_path),p,{'513100.SH':frame,'518880.SH':frame})
    engine.account.trade('513100.SH','buy',5,'2022-01-12','test','test',quantity=100)
    equity=engine.account.equity()
    cost=engine.account.positions['513100.SH']['average_price']
    engine.apply_actions(pd.Timestamp('2022-01-13'))
    assert engine.account.positions['513100.SH']['quantity']==500
    assert engine.account.positions['513100.SH']['average_price']==cost/5
    assert engine.account.equity()==pytest.approx(equity)
    engine.apply_actions(pd.Timestamp('2022-01-14'))
    assert engine.account.positions['513100.SH']['quantity']==500


def test_dividend_receivable_paid_once_and_not_awarded_to_ex_date_buyer(tmp_path,histories,plan):
    frame=histories['510300.SH'].copy()
    frame['dividend']=0.;frame['pay_date']=''
    frame.loc[pd.Timestamp('2023-06-01'),['dividend','pay_date']]=[.1,'2023-06-05']
    histories['510300.SH']=frame
    engine=Portfolio(Store(tmp_path),plan,histories)
    engine.account.trade('510300.SH','buy',3,'2023-05-31','test','test',quantity=100)
    cash=engine.account.cash
    engine.apply_actions(pd.Timestamp('2023-06-01'))
    assert engine.state['receivables'][0]['amount']==10
    assert engine.account.cash==cash
    engine.apply_actions(pd.Timestamp('2023-06-05'))
    engine.apply_actions(pd.Timestamp('2023-06-05'))
    assert engine.account.cash==cash+10 and not engine.state['receivables']
    other=Portfolio(Store(tmp_path),plan,histories)
    other.execute({'date':'2023-05-31','weights':{'510300.SH':.35},'reason':'test'},pd.Timestamp('2023-06-01'))
    assert other.account.positions
    assert not other.state['receivables']


def test_hold_matches_direct_calculation_with_fees(tmp_path,histories,plan):
    p=plan.model_copy(update={'commission_rate':0.,'min_commission':0.,'slippage_bps':0.})
    result=simulate(Store(tmp_path),p,histories,'2023-01-01','2023-12-31','hold')
    start=pd.Timestamp(result['start']); end=pd.Timestamp(result['end'])
    expected=p.starting_cash
    for symbol, frame in histories.items():
        qty=int(p.starting_cash*hold_weights(p)[symbol]/frame.loc[start].open//100*100)
        expected+=qty*(frame.loc[end].close-frame.loc[start].open)
    assert result['end_equity']==pytest.approx(expected)
    assert result['trade_count']==len(p.symbols)


def test_hold_is_a_selectable_plan_and_activates_after_strategy_change(tmp_path, histories, plan):
    selected = Plan.model_validate({**plan.model_dump(), 'method': 'hold'})
    result = simulate(Store(tmp_path), selected, histories, '2023-01-01', '2023-12-31')
    assert len(result['decisions']) == 1
    assert result['trade_count'] == len(selected.symbols)
    active = Portfolio(Store(tmp_path), plan, histories)
    active.step(pd.Timestamp('2024-01-02'), execute=False)
    restarted = Portfolio(Store(tmp_path), selected, histories, restored=active.state)
    restarted.state['period'] = None
    restarted.step(pd.Timestamp('2024-01-03'), execute=False)
    assert restarted.state['pending']['weights'] == hold_weights(selected)
    assert restarted.state['hold_plan_key'] == restarted.plan_key


def test_save_conflict_and_execution_settings_are_fingerprinted(tmp_path,plan):
    store=Store(tmp_path)
    original=store.settings()
    assert save_settings(store,plan,1)['revision']==2
    with pytest.raises(ConflictError): save_settings(store,plan,1)
    assert store.settings()==original
    assert settings(Store(tmp_path))['plan']==plan.model_dump()
    assert fingerprint(plan)==fingerprint(Plan.model_validate_json(plan.model_dump_json()))
    research = plan.model_copy(update={'research_interval': '60m', 'research_multiple': 3,
                                       'research_annual_rate': .12, 'research_maintenance_ratio': .4,
                                       'validation_start_year': 2022, 'validation_end_year': 2024})
    assert fingerprint(research) != fingerprint(plan)
    assert comparison_fingerprint(research) != comparison_fingerprint(plan)
    years_only = plan.model_copy(update={'validation_start_year': 2022, 'validation_end_year': 2024})
    assert fingerprint(years_only) == fingerprint(plan)
    assert research.model_dump()['research_multiple'] == 3
    assert research.model_dump()['validation_start_year'] == 2022


def test_verification_uses_one_market_and_execution_plan_for_both_targets(tmp_path, histories, plan, monkeypatch):
    import ai_stock_bot.v4.service as service

    store = Store(tmp_path)
    request = JobRequest(kind='portfolio_test', investment=plan, years=[2024], config=AppConfig())
    job = store.create_job(request.model_dump())
    monkeypatch.setattr(service, 'load_histories', lambda *args: (histories, audit(histories, plan.symbols, '2024-01-01', '2024-12-31')))
    calls = []

    def fake_simulate(target, current, frames, start, end, method, progress, forecast_plan=None):
        assert forecast_plan == plan
        calls.append((method, current.model_dump(), id(frames)))
        return {'method': method, 'return_pct': .1, 'per_year': [{'year': 2024, 'return_pct': .1}],
                'training': [{'cutoff': '2024-06-01', 'label_end': '2024-05-30'}] if method in {'ml', 'ml_class', 'cnn', 'cnn_class'} else []}

    monkeypatch.setattr(service, 'simulate', fake_simulate)
    report = comparison(store, job['id'], request, lambda *args: None)
    assert list(report['results']) == ['rotation', 'ml_class', 'ml', 'cnn_class', 'cnn', 'hold', 'hold_full', 'stress']
    assert len({market_id for _, _, market_id in calls}) == 1
    for method, values, _ in calls:
        if method not in {'hold', 'rotation'}:
            assert values['symbols'] == plan.symbols
            if values['commission_rate'] == plan.commission_rate:
                assert values['max_exposure'] == plan.max_exposure
                assert values['min_commission'] == plan.min_commission
                assert values['slippage_bps'] == plan.slippage_bps
    assert report['checks'][1]['ok']
    assert report['comparison_key'] == comparison_fingerprint(plan)


def test_changing_only_model_reuses_pair_results_not_original_stress(tmp_path, histories, plan, monkeypatch):
    import ai_stock_bot.v4.service as service

    store = Store(tmp_path)
    request = JobRequest(kind='portfolio_test', investment=plan, years=[2024], config=AppConfig())
    job = store.create_job(request.model_dump())
    monkeypatch.setattr(service, 'load_histories', lambda *args: (histories, audit(histories, plan.symbols, '2024-01-01', '2024-12-31')))

    def fake_simulate(target, current, frames, start, end, method, progress, forecast_plan=None):
        return {'method': method, 'return_pct': .12 if method == 'cnn_class' else .1,
                'per_year': [{'year': 2024, 'return_pct': .12 if method == 'cnn_class' else .1}],
                'training': [{'cutoff': '2024-06-01', 'label_end': '2024-05-30'}]
                if method in {'ml', 'ml_class', 'cnn', 'cnn_class'} else []}

    monkeypatch.setattr(service, 'simulate', fake_simulate)
    report = comparison(store, job['id'], request, lambda *args: None)
    report.pop('comparison_key')  # Reports saved before this reuse feature remain readable.
    store.update_job(job['id'], status='done', result=report)
    assert 'stress' in report_for(store, plan)['results']

    changed = plan.model_copy(update={'method': 'cnn_class'})
    reused = report_for(store, changed)
    assert reused['comparison_reused']
    assert reused['plan_key'] == fingerprint(changed)
    assert reused['results']['cnn_class']['return_pct'] == .12
    assert 'stress' not in reused['results']
    assert reused['checks'][2]['ok']
    assert reused['checks'][3]['pending'] and not reused['checks'][3]['ok']
    assert reused['verdict'] == '当前方案成本压力待验证'
    assert report_for(store, changed.model_copy(update={'max_exposure': .6})) is None


def test_evidence_endpoint_returns_reused_comparison_as_json(tmp_path, histories, plan, monkeypatch):
    import ai_stock_bot.v4.service as service

    store = Store(tmp_path)
    plan = plan.model_copy(update={'validation_start_year': 2024, 'validation_end_year': 2024})
    request = JobRequest(kind='portfolio_test', investment=plan, years=[2024], config=AppConfig())
    job = store.create_job(request.model_dump())
    monkeypatch.setattr(service, 'load_histories', lambda *args: (histories, audit(histories, plan.symbols, '2024-01-01', '2024-12-31')))

    def fake_simulate(target, current, frames, start, end, method, progress, forecast_plan=None):
        return {'method': method, 'return_pct': .1, 'per_year': [{'year': 2024, 'return_pct': .1}],
                'training': [{'cutoff': '2024-06-01', 'label_end': '2024-05-30'}]
                if method in {'ml', 'ml_class', 'cnn', 'cnn_class'} else []}

    monkeypatch.setattr(service, 'simulate', fake_simulate)
    store.update_job(job['id'], status='done', result=comparison(store, job['id'], request, lambda *args: None))
    save_settings(store, plan.model_copy(update={'method': 'ml_class'}), 1)
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        response = client.get('/api/v2/investment/evidence')
    assert response.status_code == 200
    result = response.json()['report']
    assert result['comparison_reused'] and result['results']['ml_class']['method'] == 'ml_class'
    assert result['checks'][3]['pending'] and 'stress' not in result['results']


def test_daily_leverage_compounds_instead_of_multiplying_total_return():
    curve = [{'date': '2025-01-02', 'equity': 90., 'exposure': 1.},
             {'date': '2025-01-03', 'equity': 100., 'exposure': 1.}]
    base, twice, triple = daily_leverage_scenarios(curve, 100., 0., maintenance_ratio=0)
    assert base['return_pct'] == pytest.approx(0.)
    assert twice['return_pct'] == pytest.approx(-.0222222222)
    assert triple['return_pct'] == pytest.approx(-.0666666667)
    assert triple['max_drawdown'] == pytest.approx(-.3)
    assert triple['worst_day'] == pytest.approx(-.3)


def test_daily_leverage_cost_and_wipeout_are_visible():
    flat = [{'date': '2025-01-02', 'equity': 100., 'exposure': 1.},
            {'date': '2025-01-03', 'equity': 100., 'exposure': 1.}]
    base, twice, triple = daily_leverage_scenarios(flat, 100., .08)
    assert base['end_equity'] == pytest.approx(100.)
    assert twice['end_equity'] == pytest.approx(100 * (1 - .08 / 252) ** 2)
    assert triple['end_equity'] == pytest.approx(100 * (1 - .16 / 252) ** 2)
    crashed = daily_leverage_scenarios([
        {'date': '2025-01-02', 'equity': 60., 'exposure': 1.},
        {'date': '2025-01-03', 'equity': 100., 'exposure': 1.},
    ], 100., 0.)
    assert crashed[2]['wiped_out_on'] == '2025-01-02'
    assert crashed[2]['end_equity'] == 0
    assert crashed[2]['max_drawdown'] == -1
    assert crashed[2]['shortfall'] == pytest.approx(20.)


def test_borrow_cost_uses_exposure_and_margin_exit_freezes_equity():
    partial = [{'date': '2025-01-02', 'equity': 100., 'exposure': .4},
               {'date': '2025-01-03', 'equity': 100., 'exposure': .4}]
    base, twice, triple = daily_leverage_scenarios(partial, 100., .08)
    assert base['financing_paid'] == twice['financing_paid'] == 0
    assert twice['end_equity'] == pytest.approx(100.)
    assert triple['mean_borrow_fraction'] == pytest.approx(.2)
    assert triple['end_equity'] == pytest.approx(100 * (1 - .2 * .08 / 252) ** 2)
    shocked = [{'date': '2025-01-02', 'equity': 90., 'exposure': 1.},
               {'date': '2025-01-03', 'equity': 100., 'exposure': 1.}]
    third = daily_leverage_scenarios(shocked, 100., 0.)[2]
    assert third['margin_exit_on'] == '2025-01-02'
    assert third['end_equity'] == pytest.approx(70.)
    assert all(point['equity'] == 70 for point in third['curve'])
    with pytest.raises(ValueError, match='持仓比例'):
        daily_leverage_scenarios([{'date': '2025-01-02', 'equity': 100.}], 100., .08)


def test_leverage_route_is_read_only_and_needs_evidence(tmp_path):
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        assert client.get('/api/v2/investment/leverage').status_code == 400
        assert client.get('/api/v2/investment/leverage?method=stress').status_code == 400
        assert client.post('/api/v2/investment/run', json={'kind': 'live_trade'}).status_code == 400


def test_leverage_route_uses_saved_report_without_changing_the_plan(tmp_path):
    store = Store(tmp_path)
    plan = Plan(validation_start_year=2025, validation_end_year=2025)
    save_settings(store, plan, 1)
    request = JobRequest(kind='portfolio_test', investment=plan, years=[2025], config=AppConfig())
    job = store.create_job(request.model_dump())
    store.update_job(job['id'], status='done', result={
        'complete': True, 'plan_key': fingerprint(plan), 'plan': plan.model_dump(),
        'start': '2025-01-01', 'end': '2025-12-31',
        'results': {'rotation': {'curve': [
            {'date': '2025-01-02', 'equity': 90000, 'exposure': 1.},
            {'date': '2025-01-03', 'equity': 100000, 'exposure': 1.},
        ]}},
    })
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        response = client.get('/api/v2/investment/leverage?method=rotation&annual_rate=0')
        current = client.get('/api/v2/investment').json()
    assert response.status_code == 200
    data = response.json()
    assert data['source_report_id'] == job['id']
    assert data['scenarios'][0]['return_pct'] == pytest.approx(0.)
    assert data['scenarios'][1]['return_pct'] == pytest.approx(-.0222222222)
    assert current['settings']['plan']['method'] == 'rotation'
    assert not current['live']['orders_enabled']


def test_execution_changes_invalidate_daily_evidence(tmp_path):
    store = Store(tmp_path)
    plan = Plan(validation_start_year=2025, validation_end_year=2025)
    save_settings(store, plan, 1)
    job = store.create_job(JobRequest(kind='portfolio_test', investment=plan, years=[2025], config=AppConfig()).model_dump())
    store.update_job(job['id'], status='done', result={
        'complete': True, 'plan_key': fingerprint(plan), 'plan': plan.model_dump(),
        'start': '2025-01-01', 'end': '2025-12-31', 'results': {'rotation': {}},
    })
    research = plan.model_copy(update={'research_interval': '60m', 'research_multiple': 3,
                                       'research_annual_rate': .11, 'research_maintenance_ratio': .35})
    save_settings(store, research, 2)
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        assert client.get('/api/v2/investment/evidence').json()['report'] is None
        current = client.get('/api/v2/investment').json()
        assert current['settings']['plan']['research_interval'] == '60m'
        assert current['settings']['plan']['research_multiple'] == 3
        assert current['settings']['plan']['validation_start_year'] == 2025
        assert current['settings']['plan']['research_annual_rate'] == .11
        assert current['live']['orders_enabled'] is False
        assert current['live']['requested_interval'] == '60m'
        assert current['live']['requested_multiple'] == 3
        assert client.post('/api/v2/investment/run', json={'kind': 'portfolio_test'}).status_code == 400


def test_changing_validation_years_hides_old_result_and_restoring_finds_it(tmp_path):
    store = Store(tmp_path)
    plan = Plan(validation_start_year=2025, validation_end_year=2025)
    save_settings(store, plan, 1)
    job = store.create_job(JobRequest(kind='portfolio_test', investment=plan, years=[2025], config=AppConfig()).model_dump())
    store.update_job(job['id'], status='done', result={
        'complete': True, 'plan_key': fingerprint(plan), 'plan': plan.model_dump(),
        'start': '2025-01-01', 'end': '2025-12-31', 'results': {'rotation': {}},
    })
    changed = plan.model_copy(update={'validation_start_year': 2024, 'validation_end_year': 2024})
    save_settings(store, changed, 2)
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        assert client.get('/api/v2/investment/evidence').json()['report'] is None
        assert client.get('/api/v2/investment/leverage').status_code == 400
    save_settings(store, plan, 3)
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        assert client.get('/api/v2/investment/evidence').json()['report']['id'] == job['id']


def test_validation_run_uses_saved_plan_years_without_a_second_year_selection(tmp_path):
    store = Store(tmp_path)
    plan = Plan(validation_start_year=2023, validation_end_year=2024)
    save_settings(store, plan, 1)
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        response = client.post('/api/v2/investment/run', json={'kind': 'portfolio_test'})
        assert response.status_code == 202
        assert response.json()['summary']['years'] == [2023, 2024]
        assert client.get('/api/v2/investment').json()['settings']['plan']['validation_end_year'] == 2024


def test_daily_leverage_report_is_not_multiplied_a_second_time(tmp_path):
    store = Store(tmp_path)
    plan = Plan(validation_start_year=2025, validation_end_year=2025,
                research_multiple=2, research_annual_rate=.12,
                research_maintenance_ratio=.42)
    save_settings(store, plan, 1)
    job = store.create_job(JobRequest(kind='portfolio_test', investment=plan, years=[2025], config=AppConfig()).model_dump())
    store.update_job(job['id'], status='done', result={
        'complete': True, 'plan_key': fingerprint(plan), 'plan': plan.model_dump(),
        'start': '2025-01-01', 'end': '2025-12-31',
        'results': {'rotation': {'curve': [
            {'date': '2025-01-02', 'equity': 100000, 'exposure': .7},
            {'date': '2025-01-03', 'equity': 101000, 'exposure': .7},
        ]}},
    })
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        response = client.get('/api/v2/investment/leverage')
    assert response.status_code == 400
    assert '重复' in response.json()['error']
    current = summary(store)
    assert current['execution']['multiple'] == 2
    assert current['settings']['plan']['research_annual_rate'] == .12
    assert current['settings']['plan']['research_maintenance_ratio'] == .42


def test_leverage_and_cadence_use_the_same_saved_frequency_curve(tmp_path):
    store = Store(tmp_path)
    plan = Plan(validation_start_year=2025, validation_end_year=2025)
    save_settings(store, plan, 1)
    test_job = store.create_job(JobRequest(kind='portfolio_test', investment=plan, years=[2025], config=AppConfig()).model_dump())
    original = [{'date': '2025-01-02', 'equity': 101000, 'exposure': .5},
                {'date': '2025-01-03', 'equity': 102000, 'exposure': .5}]
    changed = [{'date': '2025-01-02', 'equity': 90000, 'exposure': .5},
               {'date': '2025-01-03', 'equity': 80000, 'exposure': .5}]
    store.update_job(test_job['id'], status='done', result={
        'complete': True, 'plan_key': fingerprint(plan), 'plan': plan.model_dump(),
        'start': '2025-01-01', 'end': '2025-12-31',
        'results': {'rotation': {'curve': original}},
    })
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        missing = client.get('/api/v2/investment/leverage?method=rotation&cadence=daily')
        assert missing.status_code == 400
        assert '频率实验' in missing.json()['error']
        assert client.get('/api/v2/investment/leverage?cadence=hourly').status_code == 400
        frequency_job = store.create_job(JobRequest(kind='portfolio_frequency', investment=plan,
                                                    research_method='rotation', config=AppConfig()).model_dump())
        store.update_job(frequency_job['id'], status='done', result={
            'plan_key': fingerprint(plan), 'method': 'rotation', 'source_report_id': test_job['id'],
            'results': {'daily': {'base': {'curve': changed}}},
        })
        weekly = client.get('/api/v2/investment/leverage?method=rotation&cadence=weekly&annual_rate=0').json()
        daily = client.get('/api/v2/investment/leverage?method=rotation&cadence=daily&annual_rate=0').json()
        assert weekly['source_frequency_job_id'] is None
        assert daily['source_frequency_job_id'] == frequency_job['id']
        assert weekly['source_report_id'] == daily['source_report_id'] == test_job['id']
        assert daily['scenarios'][0]['return_pct'] == pytest.approx(-.2)
        assert weekly['scenarios'][0]['return_pct'] == pytest.approx(.02)
        assert settings(store)['plan']['rebalance'] == 'weekly'


def test_daily_frequency_reuses_next_open_and_never_changes_live_plan(tmp_path, histories, plan):
    store = Store(tmp_path)
    weekly = simulate(store, plan, histories, '2024-01-01', '2024-12-31', 'rotation')
    daily_plan = plan.model_copy(update={'rebalance': 'daily'})
    daily = simulate(store, daily_plan, histories, '2024-01-01', '2024-12-31', 'rotation')
    assert len(daily['decisions']) > len(weekly['decisions'])
    assert all(trade['signal_date'] < trade['date'] for trade in daily['trades'])
    assert settings(store)['plan']['rebalance'] == 'weekly'


def test_frequency_job_uses_saved_snapshot_and_persists_read_only(tmp_path, histories, plan):
    store = Store(tmp_path)
    plan = plan.model_copy(update={'validation_start_year': 2024, 'validation_end_year': 2024})
    save_settings(store, plan, 1)
    request = JobRequest(kind='portfolio_test', investment=plan, years=[2024], config=AppConfig())
    job = store.create_job(request.model_dump())
    store.update_job(job['id'], status='done', result={
        'complete': True, 'plan_key': fingerprint(plan), 'plan': plan.model_dump(),
        'start': '2024-01-01', 'end': '2024-12-31', 'results': {'rotation': {}},
    })
    write_gzip(tmp_path / 'portfolio_tests' / job['id'] / 'market.json.gz', {
        'engine': ENGINE_VERSION, 'plan_key': fingerprint(plan),
        'frames': {symbol: frame.to_json(orient='split', date_format='iso') for symbol, frame in histories.items()},
    })
    result = frequency_comparison(store, JobRequest(kind='portfolio_frequency', investment=plan,
                                                    research_method='rotation', config=AppConfig()), lambda *args: None)
    assert result['source_report_id'] == job['id']
    assert set(result['results']) == {'monthly', 'weekly', 'daily'}
    assert result['results']['daily']['base']['curve']
    assert result['results']['daily']['base']['decision_count'] > result['results']['weekly']['base']['decision_count']
    assert result['results']['daily']['stress']['fees'] >= result['results']['daily']['base']['fees']
    assert store.meta('portfolio_state') is None
    assert settings(store)['plan']['rebalance'] == 'weekly'
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        queued = client.post('/api/v2/investment/run', json={'kind': 'portfolio_frequency', 'research_method': 'rotation'})
        assert queued.status_code == 202
        store.update_job(queued.json()['id'], status='done', result=result)
        assert client.get('/api/v2/investment/frequency?method=rotation').json()['result']['source_report_id'] == job['id']
        lightweight = client.get('/api/v2/investment/frequency?method=rotation').json()['result']
        assert lightweight['results']['daily']['base']['has_curve'] is True
        assert 'curve' not in lightweight['results']['daily']['base']
        assert client.get('/api/v2/investment/frequency?method=invalid').status_code == 400
        assert client.get('/api/v2/investment').json()['live']['orders_enabled'] is False


def test_queue_blocks_settings_change(tmp_path,plan):
    store=Store(tmp_path)
    store.create_job(JobRequest(kind='portfolio_update',investment=plan,config=AppConfig()).model_dump())
    with pytest.raises(ValueError,match='模拟正在更新'): save_settings(store,plan,1)


def test_initial_update_never_backfills_and_same_day_is_idempotent(tmp_path,histories,plan,monkeypatch):
    import ai_stock_bot.v4.service as service
    class Clock(dt.datetime):
        @classmethod
        def now(cls,tz=None): return cls(2025,12,31,17,tzinfo=tz)
    monkeypatch.setattr(service.dt,'datetime',Clock)
    store=Store(tmp_path); save_settings(store,plan,1)
    update_portfolio(store,plan,lambda *a:None,histories)
    first=store.meta('portfolio_state')
    assert not first['account']['trades'] and len(first['curve'])==1
    update_portfolio(store,plan,lambda *a:None,histories)
    assert store.meta('portfolio_state')==first


@pytest.mark.parametrize('same_day', [True, False])
def test_replanning_preserves_elapsed_sessions_and_does_not_replay_orders(tmp_path, histories, plan, same_day):
    store = Store(tmp_path)
    save_settings(store, plan, 1)
    update_portfolio(store, plan, lambda *a: None, histories, now=dt.datetime(2025, 12, 29, 17))
    before = store.meta('portfolio_state')
    before['cooldown'] = 8
    store.set_meta('portfolio_state', before)
    changed = Plan.model_validate({**plan.model_dump(), 'drift_band': .06})
    save_settings(store, changed, 2)
    now = dt.datetime(2025, 12, 29 if same_day else 31, 17)
    result = update_portfolio(store, changed, lambda *a: None, histories, now=now)['state']
    assert result['cooldown'] == (8 if same_day else 6)
    assert [row['date'] for row in result['curve']] == (
        ['2025-12-29'] if same_day else ['2025-12-29', '2025-12-30', '2025-12-31'])
    assert not result['account']['trades']
    assert len(result['decisions']) == len(before['decisions']) + 1
    assert result['pending']['execute_after'] == str(now.date())
    assert result['plan_key'] == fingerprint(changed)
    repeated = update_portfolio(store, changed, lambda *a: None, histories, now=now)['state']
    assert repeated == result


def test_stale_paper_worker_cannot_write_migrations_or_return_success(tmp_path, histories, plan):
    store = Store(tmp_path)
    save_settings(store, plan, 1)
    update_portfolio(store, plan, lambda *a: None, histories, now=dt.datetime(2025, 12, 31, 17))
    before = store.meta('portfolio_state')
    before['pending'].pop('execute_after')
    store.set_meta('portfolio_state', before)
    save_settings(store, plan.model_copy(update={'drift_band': .06}), 2)
    with pytest.raises(ValueError, match='投资计划已更改'):
        update_portfolio(store, plan, lambda *a: None, histories, now=dt.datetime(2025, 12, 31, 17))
    assert store.meta('portfolio_state') == before


def test_latest_incomplete_report_does_not_hide_completed_evidence(tmp_path, plan):
    store = Store(tmp_path)
    request = JobRequest(kind='portfolio_test', investment=plan).model_dump()
    first = store.create_job(request)
    report = {'plan_key': fingerprint(plan), 'complete': True, 'start': '2021-01-01', 'end': '2025-12-31'}
    store.update_job(first['id'], status='done', result=report)
    later = store.create_job(request)
    store.update_job(later['id'], status='done', result={**report, 'complete': False})
    assert report_for(store, plan)['id'] == first['id']


def test_ai_review_rules_follow_actual_plan_instead_of_legacy_template():
    from ai_stock_bot.v4.schema import FIXED_PROFILE_PRESETS
    from ai_stock_bot.v4.service import review_rules

    basic = review_rules(Plan())
    assert '60和120' in basic['momentum']
    for profile in ('conservative', 'balanced', 'aggressive'):
        plan = Plan(**{k: v for k, v in FIXED_PROFILE_PRESETS[profile].items() if k != 'label'},
                    profile=profile, preset_version=1)
        rules = review_rules(plan)
        assert '252' in rules['momentum']
        assert '120日均线' not in rules['momentum']
        assert rules['allocation_policy'] == plan.allocation_policy
        assert rules['execution_policy'] == plan.execution_policy
        assert rules['rebalance'] == plan.rebalance
        assert '回退趋势' in rules['model']
        if plan.allocation_policy != 'legacy':
            assert '不单独否决核心持仓' in rules['ranking']
    hold = review_rules(Plan(method='hold'))
    assert '不使用择时' in hold['momentum']
    assert '不使用自动回撤暂停' in hold['pause']
    fast = review_rules(Plan(trend_signal='multi_horizon', signal_policy='fast_confirmed'))
    assert '至少三个' in fast['momentum'] and '60日均线' in fast['momentum']


def test_reset_checks_all_active_paper_jobs_not_just_latest_page(tmp_path, plan):
    store = Store(tmp_path)
    pending = store.create_job(JobRequest(kind='portfolio_update', investment=plan).model_dump())
    for _ in range(101):
        job = store.create_job(JobRequest(kind='portfolio_test', investment=plan).model_dump())
        store.update_job(job['id'], status='done')
    assert pending['id'] not in [j['id'] for j in store.jobs(100)['items']]
    state = {'account': {'cash': 100000}}
    store.set_meta('portfolio_state', state)
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        assert client.post('/api/v2/investment/reset').status_code == 400
    assert store.meta('portfolio_state') == state


def test_ai_review_with_obsolete_rules_is_not_presented_as_current(tmp_path, histories, plan):
    from ai_stock_bot.v4.service import review_rules
    store = Store(tmp_path)
    save_settings(store, plan, 1)
    update_portfolio(store, plan, lambda *a: None, histories, now=dt.datetime(2025, 12, 31, 17))
    review = {'plan_key': fingerprint(plan), 'as_of': '2025-12-31', 'input': {'rules': {'old': True}}}
    store.set_meta('portfolio_review', review)
    assert summary(store)['ai_review'] is None
    review['input']['rules'] = review_rules(plan)
    store.set_meta('portfolio_review', review)
    assert summary(store)['ai_review'] == review


def test_reset_archive_and_clear_roll_back_together(tmp_path):
    store = Store(tmp_path)
    state = {'account': {'cash': 100000}}
    store.set_meta('portfolio_state', state)
    store.set_meta('portfolio_review', {'summary': 'fixture'})
    with store.connect() as c:
        c.execute("CREATE TRIGGER fail_reset BEFORE UPDATE ON meta "
                  "WHEN NEW.key='portfolio_state' AND NEW.value='null' "
                  "BEGIN SELECT RAISE(ABORT,'fixture reset failure'); END")
    with TestClient(create_app(tmp_path, start_manager=False), raise_server_exceptions=False) as client:
        assert client.post('/api/v2/investment/reset').status_code == 500
    assert store.meta('portfolio_state') == state
    assert store.meta('portfolio_review') == {'summary': 'fixture'}
    with store.connect() as c:
        assert c.execute("SELECT count(*) FROM meta WHERE key LIKE 'portfolio_archive:%'").fetchone()[0] == 0
        c.execute('DROP TRIGGER fail_reset')
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        assert client.post('/api/v2/investment/reset').json() == {'archived': True}
    assert store.meta('portfolio_state') is None and store.meta('portfolio_review') is None
    with store.connect() as c:
        assert json.loads(c.execute("SELECT value FROM meta WHERE key LIKE 'portfolio_archive:%'").fetchone()[0]) == state


def test_scheduler_once_per_day_when_explicitly_enabled(tmp_path):
    store=Store(tmp_path)
    stamp=dt.datetime(2025,12,30,17)
    enqueue_daily(store,stamp)
    assert store.jobs()['total']==0
    save_settings(store,Plan(auto_update=True),1)
    enqueue_daily(store,stamp); enqueue_daily(store,stamp)
    assert store.jobs()['total']==2
    assert {item['kind'] for item in store.jobs()['items']} == {'portfolio_update', 'intraday_refresh'}


def test_delayed_data_cannot_create_retroactive_forward_trades(tmp_path,histories,plan,monkeypatch):
    import ai_stock_bot.v4.service as service
    class Clock(dt.datetime):
        @classmethod
        def now(cls,tz=None): return cls(2025,12,31,17,tzinfo=tz)
    monkeypatch.setattr(service.dt,'datetime',Clock)
    store=Store(tmp_path);save_settings(store,plan,1)
    past={s:f.loc[:'2025-12-29'] for s,f in histories.items()}
    update_portfolio(store,plan,lambda *a:None,past)
    assert store.meta('portfolio_state')['pending']['execute_after']=='2025-12-31'
    assert store.meta('portfolio_state')['curve'][0]['date']=='2025-12-31'
    update_portfolio(store,plan,lambda *a:None,histories)
    assert not store.meta('portfolio_state')['account']['trades']


def test_api_full_plan_validation_and_result_scope(tmp_path):
    with TestClient(create_app(tmp_path,start_manager=False)) as client:
        first=client.get('/api/v2/investment').json()
        assert first['broker']['can_trade'] is False
        assert first['live']['status'] == 'not_connected'
        assert first['live']['equity'] is None and first['live']['cash'] is None
        assert first['live']['positions'] is None and first['live']['open_orders'] is None
        assert not first['live']['orders_enabled'] and not first['live']['automatic_orders_enabled']
        plan=first['settings']['plan'];plan['max_exposure']=.1
        assert client.put('/api/v2/investment/plan',json={'plan':plan,'revision':1}).status_code==422
        assert client.post('/api/v2/investment/run',json={'kind':'live_trade'}).status_code==400
        assert client.get('/api/v2/investment/evidence').json()['report'] is None
        job = client.post('/api/v2/investment/run',json={'kind':'portfolio_test'}).json()
        assert job['summary']['engine'] == 'portfolio'
        assert job['summary']['method'] == 'rotation'
        assert 'profile' not in job['summary']
        plan['max_exposure'] = .7
        plan['method'] = 'cnn'
        assert client.put('/api/v2/investment/plan', json={'plan': plan, 'revision': 1}).status_code == 200
        assert client.get('/api/v2/investment').json()['settings']['plan']['method'] == 'cnn'
        plan['method'] = 'ml_class'
        plan['classification_threshold'] = .62
        assert client.put('/api/v2/investment/plan', json={'plan': plan, 'revision': 2}).status_code == 200
        persisted = client.get('/api/v2/investment').json()['settings']['plan']
        assert persisted['method'] == 'ml_class' and persisted['classification_threshold'] == .62


def test_research_and_investment_prices_are_isolated(tmp_path,histories):
    store=Store(tmp_path); legacy=Market(store)
    original=histories['510300.SH']
    legacy.write('510300.SH',original,'fixture')
    current=PortfolioMarket(store)
    modified=original.copy()
    modified[['open','high','low','close','adjusted']]*=2
    legacy.write('510300.SH',modified,'fixture')
    assert current.read('510300.SH').close.iloc[-1]==original.close.iloc[-1]
    current.write('510300.SH',original,'investment fixture')
    assert legacy.read('510300.SH').close.iloc[-1]==modified.close.iloc[-1]
    assert PortfolioMarket(store).read('510300.SH').close.iloc[-1]==original.close.iloc[-1]


def test_append_latest_requires_matching_raw_price_basis(tmp_path,histories,monkeypatch):
    store=Store(tmp_path); market=PortfolioMarket(store)
    frame=histories['510300.SH']; old=frame.loc[:'2025-12-29']
    market.write('510300.SH',old,'fixture')
    recent=frame.loc['2025-11-28':].copy()
    monkeypatch.setattr(market,'_yahoo',lambda *a:recent)
    assert market.refresh_tail('510300.SH',market.read('510300.SH'),dt.date(2025,12,31))
    assert market.read('510300.SH').index[-1]==pd.Timestamp('2025-12-31')
    market.write('510300.SH',old,'fixture')
    wrong=recent.copy();wrong.close*=2
    monkeypatch.setattr(market,'_yahoo',lambda *a:wrong)
    assert not market.refresh_tail('510300.SH',old,dt.date(2025,12,31))


def hourly_fixture(store, symbol='518880.SH', days=12):
    from ai_stock_bot.v4.data import intraday_table
    intraday_table(store)
    rows = []
    price = 5.
    for day in pd.bdate_range('2025-01-02', periods=days):
        for hour in (10, 11, 14, 15):
            price *= 1.002 if len(rows) < days * 2 else .999
            stamp = f'{day.date()} {hour:02d}:30:00' if hour < 12 else f'{day.date()} {hour:02d}:00:00'
            rows.append((symbol, stamp, EXECUTION_INTERVAL, price, price * 1.002,
                         price * .998, price, 10_000_000., 'fixture 60m / raw'))
    with store.connect() as connection:
        connection.executemany('INSERT INTO intraday_bars VALUES(?,?,?,?,?,?,?,?,?)', rows)
    return rows


def test_intraday_replay_is_causal_costed_and_read_only(tmp_path):
    store = Store(tmp_path)
    rows = hourly_fixture(store)
    plan = Plan(symbols=['518880.SH', '511260.SH'], profile='custom',
                max_exposure=.7, max_weight=.7)
    save_settings(store, plan, 1)
    result = intraday_research(store, plan, '518880.SH')
    assert result['status'] == 'exploratory'
    assert result['coverage']['bars'] == len(rows)
    assert result['coverage']['sessions'] == 12
    assert result['assumptions']['live_orders'] is False
    assert len(result['scenarios']) == 3
    for row in result['scenarios']:
        base, stress = row['base'], row['stress']
        assert base['trades']
        assert base['trades'][0]['stamp'] >= rows[8][1]
        assert all(trade['quantity'] % 100 == 0 for trade in base['trades'])
        assert stress['fees'] >= base['fees']
        assert base['return_pct'] == pytest.approx(base['end_equity'] / plan.starting_cash - 1)
    assert result['scenarios'][1]['base']['interest'] > 0
    assert store.meta('portfolio_state') is None
    assert settings(store)['plan'] == plan.model_dump()


def test_intraday_replay_requires_t0_and_consistent_data(tmp_path):
    store = Store(tmp_path)
    hourly_fixture(store, symbol='510300.SH')
    plan = Plan(symbols=['510300.SH', '518880.SH'])
    with pytest.raises(ValueError, match=r'T\+0'):
        intraday_research(store, plan, '510300.SH')
    hourly_fixture(store)
    with store.connect() as connection:
        connection.execute("UPDATE intraday_bars SET source='other' WHERE symbol='518880.SH' AND stamp=(SELECT min(stamp) FROM intraday_bars WHERE symbol='518880.SH')")
    with pytest.raises(ValueError, match='混用'):
        intraday_research(store, plan, '518880.SH')


def test_intraday_route_never_enables_orders(tmp_path):
    store = Store(tmp_path)
    hourly_fixture(store)
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        result = client.get('/api/v2/investment/intraday-research?symbol=518880.SH')
        bad = client.get('/api/v2/investment/intraday-research?symbol=510300.SH')
        account = client.get('/api/v2/investment').json()
    assert result.status_code == 200 and result.json()['result'] is None
    assert bad.status_code == 400
    assert account['live']['orders_enabled'] is False
    assert store.meta('portfolio_state') is None


def test_intraday_uses_saved_symbol_and_cost_assumptions(tmp_path):
    store = Store(tmp_path)
    hourly_fixture(store, symbol='511260.SH')
    plan = Plan(research_interval='60m', research_symbol='511260.SH',
                research_multiple=2, research_annual_rate=.13,
                research_maintenance_ratio=.41)
    save_settings(store, plan, 1)
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        queued = client.post('/api/v2/investment/run', json={'kind': 'intraday_test'})
        assert queued.status_code == 202
        assert queued.json()['id'] in [j['id'] for j in client.get('/api/v2/investment').json()['jobs']]
        from ai_stock_bot.v2.jobs import execute_job
        execute_job(tmp_path, queued.json()['id'])
        response = client.get('/api/v2/investment/intraday-research')
    assert response.status_code == 200
    result = response.json()['result']
    assert result['symbol'] == '511260.SH'
    assert result['assumptions']['borrow_rate'] == .13
    assert result['assumptions']['maintenance_ratio'] == .41
    assert settings(store)['plan']['research_multiple'] == 2
    assert store.meta('portfolio_state') is None
    saved = settings(store)
    save_settings(store, plan.model_copy(update={'intraday_cost_buffer': 2.0}), saved['revision'])
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        assert client.get('/api/v2/investment/intraday-research').json()['result'] is None


def test_intraday_refresh_keeps_existing_source_and_history(tmp_path, monkeypatch):
    import ai_stock_bot.v4.data as market_data
    store = Store(tmp_path)
    market_data.intraday_table(store)
    source = 'Yahoo 60m session-close / raw'
    with store.connect() as connection:
        connection.execute('INSERT INTO intraday_bars VALUES(?,?,?,?,?,?,?,?,?)',
                           ('518880.SH', '2025-01-02 10:30:00', '60m', 5, 5.1, 4.9, 5, 100000, source))
    new = pd.DataFrame([{'stamp': '2025-01-03 10:30:00', 'open': 5, 'high': 5.1,
                         'low': 4.9, 'close': 5.05, 'volume': 100000}])
    monkeypatch.setattr(market_data, 'hourly_yahoo', lambda _: new)
    result = market_data.refresh_intraday(store, ['518880.SH'], lambda *args: None)
    assert result['coverage'][0]['bars'] == 1
    assert intraday_research(store, Plan(), '518880.SH')['coverage']['source'].startswith('Yahoo 30m')
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM intraday_bars WHERE symbol=? AND interval='60m'", ('518880.SH',)).fetchone()[0] == 1
    monkeypatch.setattr(market_data, 'hourly_yahoo', lambda _: (_ for _ in ()).throw(RuntimeError('offline')))
    with pytest.raises(RuntimeError, match='小时执行行情'):
        market_data.refresh_intraday(store, ['518880.SH'], lambda *args: None)
    assert market_data.intraday_coverage(store)[0]['bars'] == 1


def test_yahoo_half_hours_are_aggregated_into_four_completed_exchange_hours(monkeypatch):
    import ai_stock_bot.v4.data as market_data
    starts = ['09:30', '10:00', '10:30', '11:00', '11:30', '12:00',
              '13:00', '13:30', '14:00', '14:30', '15:00']
    stamps = [int(pd.Timestamp(f'2025-01-07 {clock}', tz='Asia/Shanghai').timestamp())
              for clock in starts]
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {'chart': {'result': [{'timestamp': stamps, 'indicators': {'quote': [{
                'open': [5 + i * .01 for i in range(len(starts))],
                'high': [5.02 + i * .01 for i in range(len(starts))],
                'low': [4.99 + i * .01 for i in range(len(starts))],
                'close': [5.01 + i * .01 for i in range(len(starts))],
                'volume': [10000] * len(starts),
            }]}}]}}
    monkeypatch.setattr(market_data.requests, 'get', lambda *args, **kwargs: Response())
    frame = market_data.hourly_yahoo('518880.SH')
    assert frame.stamp.dt.strftime('%H:%M').tolist() == ['10:30', '11:30', '14:00', '15:00']
    assert frame.volume.tolist() == [20000] * 4
    assert frame.iloc[0].open == 5
    assert frame.iloc[1].close == pytest.approx(5.04)


def test_daily_multiple_changes_actual_fills_cash_and_interest(tmp_path, histories):
    store = Store(tmp_path)
    base = Plan(method='hold', symbols=list(histories), profile='custom',
                max_exposure=.9, max_weight=.6, commission_rate=0.,
                min_commission=0., slippage_bps=0.)
    leveraged = base.model_copy(update={'research_multiple': 3})
    normal = simulate(store, base, histories, '2023-01-01', '2023-03-31')
    actual = simulate(store, leveraged, histories, '2023-01-01', '2023-03-31')
    assert actual['trade_count'] >= normal['trade_count'] > 0
    assert any(point['cash'] < 0 and point['exposure'] > 1 for point in actual['curve'])
    assert actual['interest_paid'] > 0
    assert all(point['exposure'] <= 3.01 for point in actual['curve'] if point['equity'] > 0)
    assert all(trade['signal_date'] < trade['date'] for trade in actual['trades'])
    assert normal['interest_paid'] == 0
    assert all(point['cash'] >= 0 for point in normal['curve'])


def test_execution_mode_switch_requires_account_archive(tmp_path):
    store = Store(tmp_path)
    plan = Plan()
    save_settings(store, plan, 1)
    store.set_meta('portfolio_state', {
        'plan_key': fingerprint(plan), 'mode': 'daily', 'last_date': '2025-01-02',
        'account': {'cash': 100000, 'equity': 100000, 'positions': {}, 'trades': [], 'fees': 0},
        'curve': [], 'decisions': [],
    })
    changed = plan.model_copy(update={'research_interval': '60m', 'research_multiple': 2})
    with pytest.raises(ValueError, match='归档'):
        save_settings(store, changed, 2)
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        assert client.post('/api/v2/investment/reset').json()['archived'] is True
        assert client.put('/api/v2/investment/plan', json={
            'plan': changed.model_dump(), 'revision': 2}).status_code == 200
        assert client.get('/api/v2/investment').json()['execution']['interval'] == '60m'


def test_hourly_forward_paper_only_fills_after_order_and_charges_interest(tmp_path):
    store = Store(tmp_path)
    plan = Plan(symbols=['518880.SH', '511260.SH'], research_interval='60m', intraday_strategy='legacy',
                research_multiple=3, profile='custom', max_exposure=.7, max_weight=.6)
    save_settings(store, plan, 1)
    rows = []
    price = 5.
    for day in ('2025-01-02', '2025-01-03', '2025-01-06'):
        for clock in ('10:30:00', '11:30:00', '14:30:00', '15:00:00'):
            price *= 1.01
            rows.append({'stamp': f'{day} {clock}', 'open': price, 'high': price * 1.001,
                         'low': price * .999, 'close': price, 'volume': 10_000_000.,
                         'source': 'fixture 60m / raw'})
    first = update_hourly_portfolio(store, plan, lambda *args: None, rows=rows,
                                    now=dt.datetime(2025, 1, 6, 15, 5))['state']
    assert first['account']['trades'] == []
    assert first['pending']['buy'] is True
    assert first['last_date'] == '2025-01-06 15:00:00'
    same = update_hourly_portfolio(store, plan, lambda *args: None, rows=rows,
                                   now=dt.datetime(2025, 1, 6, 15, 6))['state']
    assert same == first
    price *= 1.01
    rows.append({'stamp': '2025-01-07 10:30:00', 'open': price, 'high': price * 1.001,
                 'low': price * .999, 'close': price, 'volume': 10_000_000.,
                 'source': 'fixture 60m / raw'})
    second = update_hourly_portfolio(store, plan, lambda *args: None, rows=rows,
                                     now=dt.datetime(2025, 1, 7, 10, 40))['state']
    assert len(second['account']['trades']) == 1
    assert second['account']['trades'][0]['signal_date'] < second['account']['trades'][0]['stamp']
    assert second['account']['cash'] < 0
    for clock in ('11:30:00', '14:30:00', '15:00:00'):
        price *= 1.001
        rows.append({'stamp': f'2025-01-07 {clock}', 'open': price,
                     'high': price * 1.001, 'low': price * .999, 'close': price,
                     'volume': 10_000_000., 'source': 'fixture 60m / raw'})
    last = update_hourly_portfolio(store, plan, lambda *args: None, rows=rows,
                                   now=dt.datetime(2025, 1, 7, 15, 5))['state']
    assert last['interest_paid'] > 0
    assert last['account']['borrowed'] > 0
    assert len(last['account']['trades']) == 1
    for day in ('2025-01-08', '2025-01-09'):
        rows.append({'stamp': f'{day} 15:00:00', 'open': price,
                     'high': price * 1.001, 'low': price * .999, 'close': price,
                     'volume': 10_000_000., 'source': 'fixture 60m / raw'})
    resumed = update_hourly_portfolio(store, plan, lambda *args: None, rows=rows,
                                      now=dt.datetime(2025, 1, 9, 15, 5))['state']
    assert resumed['interest_paid'] > last['interest_paid'] * 2.5
    assert len(resumed['account']['trades']) == 1


def test_hourly_scheduler_queues_per_completed_slot(tmp_path):
    store = Store(tmp_path)
    plan = Plan(auto_update=True, research_interval='60m')
    save_settings(store, plan, 1)
    enqueue_daily(store, dt.datetime(2025, 1, 7, 10, 35))
    assert store.jobs()['total'] == 0
    enqueue_daily(store, dt.datetime(2025, 1, 7, 10, 45))
    enqueue_daily(store, dt.datetime(2025, 1, 7, 10, 46))
    assert store.jobs()['total'] == 1
    first = store.jobs()['items'][0]
    store.update_job(first['id'], status='done')
    enqueue_daily(store, dt.datetime(2025, 1, 7, 11, 45))
    assert store.jobs()['total'] == 2
    assert all(job['kind'] == 'portfolio_update' for job in store.jobs()['items'])
