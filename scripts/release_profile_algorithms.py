"""Audit family selection, verify production signals, and publish read-only findings."""
import argparse
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from compare_profile_algorithms import utility
from research_balanced_preset import sha
from research_combinations import get_histories
from research_resilient_execution import ROOT, SNAPSHOT, load, write, FrozenForecasts
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.schema import Plan, fingerprint
from ai_stock_bot.v4.model_recipes import RECIPE_LABELS, recipe_catalog
from ai_stock_bot.v4.portfolio import Portfolio, simulate
from ai_stock_bot.v4.research_forecast import ResearchForecasts


def audit(directory):
    spec, result = load(directory/'protocol.json'), load(directory/'results.json')
    assert result.get('completed_at') and sha(SNAPSHOT) == spec['market_sha256']
    for file, digest in spec['source_sha256'].items():
        assert sha(directory/'source'/file) == digest
    for name, digest in load(directory/'forecast_manifest.json').items():
        assert sha(directory/'models'/name/'panel.json') == digest
    count, decisions = 0, 0
    for file in (directory/'cases').glob('*.result.json'):
        record = load(file)
        request = load(file.with_name(file.name.replace('.result.json', '.json')))
        p = request['plan']
        eq = np.r_[p['starting_cash'], [r['equity'] for r in record['curve']]]
        days = ((pd.Timestamp(record['end'])-pd.Timestamp(record['start'])).days+1)/365.25
        net = (eq[-1]-record['exit_cost'])/eq[0]
        np.testing.assert_allclose([record['liquidation_cagr'], record['liquidation_return'], record['max_drawdown']],
            [net**(1/days)-1, net-1, (eq/np.maximum.accumulate(eq)-1).min()], atol=1e-10)
        np.testing.assert_allclose(eq[0]+sum(record['price_pnl'].values())+record['dividends_paid']
            +record['receivables']-record['fees']-record['interest_paid'], eq[-1], atol=1e-6)
        assert request['period'][0] <= record['start'] <= record['end'] <= request['period'][1]
        assert all(t['signal_date'] < t['date'] for t in record['trades'])
        assert all(not r.get('label_end') or r['label_end'] < r['cutoff'] for r in record['training'])
        if request['method'] == 'rotation':
            assert record['training'] == [] and record['model_decisions'] == 0
        for d in record['decisions']:
            budget = d.get('risk_budget', {})
            assert budget.get('status') != 'failed'
            if budget.get('status') == 'optimized':
                assert budget['target_gross'] <= p['max_exposure']+1e-6
                assert budget['estimated_volatility'] <= budget['target_volatility']+1e-5
            decisions += 1
        count += 1
    expected = 2*len(result['development'])+2*len(result['selection'])+14*len(result['finalists'])
    assert count == expected
    for profile, limits in spec['limits'].items():
        def dev(key):
            return utility(result['development'][key], limits)
        families = {family: max([f'{profile}--{name}' for name in names], key=dev)
                    for family, names in spec['families'].items()}
        assert families == result['family_winners'][profile]
        baseline = f'{profile}--hgb20_regularized'
        def score(key):
            a, b = dev(key), utility(result['selection'][key], limits)
            return a[0] and b[0], .5*(a[1]+b[1]), key == baseline
        assert result['locked'][profile] == max(dict.fromkeys([*families.values(), baseline]), key=score)
        original = load(directory/'cases'/f'full-{baseline}-base.result.json')
        reference = (ROOT/'docs/reports/production-recipe-20260930/continuous.json' if profile == 'aggressive'
            else ROOT/'docs/reports/core-profiles-20260930'/f'production-{profile}-full.json')
        np.testing.assert_allclose([p['equity'] for p in original['curve']], [p['equity'] for p in load(reference)['curve']], atol=1e-7)
    value = {'passed': True, 'replays': count, 'decisions': decisions, 'pure_trend_has_no_model_calls': True,
        'baseline_reproduced': True, 'family_and_algorithm_selections_reproduced': True,
        'forward_proven': False, 'initial_cnn_import_failed_once': True,
        'cnn_retry': 'Identical-input isolated retry completed; native failure cause not established.'}
    write(directory/'audit.json', value)
    print(value, flush=True)


def verify(directory):
    spec, result = load(directory/'protocol.json'), load(directory/'results.json')
    histories = get_histories()
    store = Store(directory/'production-store')
    cache = ROOT/'docs/reports/production-recipe-20260930/isolated-store/portfolio_models'
    if not (store.root/'portfolio_models').exists():
        shutil.copytree(cache, store.root/'portfolio_models')
    checks = {}
    for recipe in spec['models']:
        panel = load(directory/'models'/recipe/'panel.json')
        accepted = [r for r in panel['reports'] if r['status'] == 'model']
        cutoff = pd.Timestamp((accepted or panel['reports'])[0]['cutoff'])
        eligible = sorted(set.intersection(*(set(f.index) for f in histories.values())))
        dates = [d for d in eligible if cutoff <= d < cutoff+pd.offsets.MonthBegin()]
        assert dates
        date = dates[-1]
        actual = ResearchForecasts(store, histories, recipe=recipe)
        predicted, receipt = actual.at(date)
        reference = FrozenForecasts(panel)
        wanted, ref = reference.at(date)
        assert receipt['status'] == ref['status'] and predicted.keys() == wanted.keys()
        for s in wanted:
            np.testing.assert_allclose(predicted[s], wanted[s], rtol=1e-7, atol=1e-8)
        for profile in spec['limits']:
            plan = Plan.model_validate(spec['cases'][f'{profile}--{recipe}'])
            a = Portfolio(store, plan, histories, method='rotation')
            b = Portfolio(store, plan, histories, method='rotation')
            a.method = b.method = 'ml'
            a.forecasts, b.forecasts = actual, reference
            aa, bb = a.make_plan(date), b.make_plan(date)
            assert aa['weights'].keys() == bb['weights'].keys()
            for s in aa['weights']:
                np.testing.assert_allclose(aa['weights'][s], bb['weights'][s], rtol=1e-6, atol=1e-8)
        checks[recipe] = {'date': str(date.date()), 'predictions': len(predicted), 'status': receipt['status'], 'profiles': 3}
        print(f'PRODUCTION RECIPE {recipe}: {checks[recipe]}', flush=True)
    for profile, decision in result['decisions'].items():
        key = decision['recommended']
        plan = Plan.model_validate(spec['cases'][key])
        real = simulate(store, plan, histories, *spec['periods']['full'])
        frozen = load(directory/'cases'/f'full-{key}-base.result.json')
        assert [p['date'] for p in real['curve']] == [p['date'] for p in frozen['curve']]
        np.testing.assert_allclose([p['equity'] for p in real['curve']], [p['equity'] for p in frozen['curve']], atol=1e-7)
        write(directory/f'production-{profile}.json', real)
        print(f'PRODUCTION PROFILE {profile}: {len(real["curve"])} points', flush=True)
    write(directory/'production-parity.json', {'passed': True, 'recipes': checks, 'full_profiles': list(result['decisions'])})


def report(directory):
    spec, result = load(directory/'protocol.json'), load(directory/'results.json')
    assert load(directory/'audit.json')['passed'] and load(directory/'production-parity.json')['passed']
    profiles = {}
    for profile, decision in result['decisions'].items():
        rows, variants = [], []
        for family, key in result['family_winners'][profile].items():
            recipe = key.split('--')[1]
            dev, selection = result['development'][key]['base'], result['selection'][key]['base']
            label = '纯趋势' if recipe == 'trend' else RECIPE_LABELS[recipe]
            rows.append({'key': key, 'label': label, 'development_cagr': dev['liquidation_cagr'],
                'selection_cagr': selection['liquidation_cagr'], 'selection_drawdown': selection['max_drawdown'],
                'verdict': '保留' if key == decision['recommended'] else '综合未胜出'})
            for name in spec['families'][family]:
                trial = result['development'][f'{profile}--{name}']['base']
                variants.append({'label': '纯趋势' if name == 'trend' else RECIPE_LABELS[name],
                    'cagr': trial['liquidation_cagr'], 'drawdown': trial['max_drawdown'],
                    'model_decisions': trial['model_decisions']})
        key = decision['recommended']
        plan = Plan.model_validate(spec['cases'][key])
        data = result['finalists'][key]
        full = data['full']['base']
        conclusion = '本轮独立比较后保留 HGB 强正则；不要求各档使用相同模型。'
        if profile == 'aggressive' and not decision['checks']['return_target']:
            conclusion += ' 激进档仍未达到本次20%净年化及近期不亏门槛，不能视为盈利目标已达成。'
        profiles[profile] = {'recommended_key': key, 'plan_key': fingerprint(plan), 'rows': rows,
            'parameter_trials': variants, 'conclusion': conclusion, 'checks': decision['checks'],
            'metrics': {'cagr': full['liquidation_cagr'], 'drawdown': full['max_drawdown'],
                        'recent_return': data['recent']['base']['liquidation_return']},
            'forward_proven': False}
    value = {'study': directory.name, 'completed_at': result['completed_at'], 'profiles': profiles,
        'replays': load(directory/'audit.json')['replays'], 'candidate_count': len(spec['cases']),
        'recipe_count_including_trend': len(spec['models'])+1, 'market_sha256': spec['market_sha256'],
        'limitations': spec['limitations']}
    write(directory/'summary.json', value)
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--publish', type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.verify:
        verify(directory)
    elif args.publish:
        store = Store(args.publish)
        store.set_meta('profile_algorithm_study', report(directory))
        print('Published research findings only; no settings, accounts or orders changed.')
    else:
        audit(directory)
