"""Independently reproduce the defensive comparison and its promotion decision."""
import argparse
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from audit_resilient_execution import block_difference
from refine_conservative_core import selection_score, promotion_checks
from research_balanced_preset import sha
from research_core_profiles import checks
from research_resilient_execution import ROOT, SOURCE, SNAPSHOT, load, write


def audit(directory):
    spec, result = load(directory/'protocol.json'), load(directory/'results.json')
    assert result.get('completed_at')
    assert sha(SNAPSHOT) == spec['market_sha256'] and sha(SOURCE) == spec['forecast_sha256']
    for file, digest in spec['source_sha256'].items():
        assert sha(directory/'source'/file) == digest
    files = list((directory/'cases').glob('*.result.json'))
    assert len(files) == 2*len(spec['cases'])+12*len(result['finalists'])
    decisions = 0
    for file in files:
        row = load(file)
        request = load(file.with_name(file.name.replace('.result.json', '.json')))
        plan = request['plan']
        assert plan['research_multiple'] == 1 and plan['target_volatility'] == .04
        assert plan['max_exposure'] == .7 and plan['max_weight'] == .35 and plan['drawdown_pause'] == .06
        eq = np.r_[plan['starting_cash'], [r['equity'] for r in row['curve']]]
        dates = [r['date'] for r in row['curve']]
        assert dates == sorted(set(dates)) and request['period'][0] <= row['start'] <= row['end'] <= request['period'][1]
        net = (eq[-1]-row['exit_cost'])/eq[0]
        years = ((pd.Timestamp(row['end'])-pd.Timestamp(row['start'])).days+1)/365.25
        np.testing.assert_allclose([net-1, net**(1/years)-1, (eq/np.maximum.accumulate(eq)-1).min()],
            [row['liquidation_return'], row['liquidation_cagr'], row['max_drawdown']], atol=1e-10)
        np.testing.assert_allclose(eq[0]+sum(row['price_pnl'].values())+row['dividends_paid']
            +row['receivables']-row['fees']-row['interest_paid'], eq[-1], atol=1e-6)
        assert row['interest_paid'] == 0 and not row['insolvent']
        assert all(t['signal_date'] < t['date'] for t in row['trades'])
        assert all(not r.get('label_end') or r['label_end'] < r['cutoff'] for r in row['training'])
        for decision in row['decisions']:
            budget = decision.get('risk_budget', {})
            assert budget.get('status') != 'failed'
            if budget.get('policy') == 'defensive_v1' and budget.get('status') == 'optimized':
                assert budget['target_gross'] <= plan['max_exposure']+1e-6
                assert budget['estimated_volatility'] <= budget['target_volatility']+1e-5
                assert budget['risky_exposure'] <= budget['risky_limit']+1e-6
            decisions += 1
    baseline = load(directory/'cases/full-original-base.result.json')
    reference = load(ROOT/spec['baseline_reference'])
    np.testing.assert_allclose([r['equity'] for r in baseline['curve']], [r['equity'] for r in reference['curve']], atol=1e-7)
    winner = max(spec['cases'], key=lambda k: (*selection_score(result['development'][k]), k == 'original'))
    assert winner == result['locked']
    verdict = checks(result['finalists'][winner], result['independent_years'][winner], spec['limits'])
    promoted = promotion_checks(result['finalists'][winner], result['finalists']['original'], all(verdict.values()))
    assert verdict == result['historical_checks'] and promoted == result['promotion']
    assert all(promoted.values()) == result['promoted']
    candidate = load(directory/'cases'/f'full-{winner}-base.result.json')
    value = {'passed': True, 'replays': len(files), 'decisions': decisions, 'baseline_reproduced': True,
        'winner': winner, 'promoted': result['promoted'], 'forward_proven': False,
        'uncertainty': [block_difference(candidate, baseline, n) for n in (20, 60)],
        'caveat': 'Exploratory paired intervals, not adjusted for selection or repeated historical research.'}
    write(directory/'audit.json', value)
    print(value, flush=True)


def verify_production(directory):
    from research_combinations import get_histories
    from ai_stock_bot.v2.store import Store
    from ai_stock_bot.v4.portfolio import simulate
    from ai_stock_bot.v4.schema import Plan
    spec, result = load(directory/'protocol.json'), load(directory/'results.json')
    assert load(directory/'audit.json')['passed']
    store = Store(directory/'production-store')
    if not (store.root/'portfolio_models').exists():
        shutil.copytree(ROOT/'docs/reports/profile-algorithms-20260930/production-store/portfolio_models',
                        store.root/'portfolio_models')
    histories = get_histories()
    counts = {}
    for key in dict.fromkeys(['original', result['locked']]):
        plan = Plan.model_validate(spec['cases'][key])
        actual = simulate(store, plan, histories, *spec['periods']['full'])
        expected = load(directory/'cases'/f'full-{key}-base.result.json')
        assert [r['date'] for r in actual['curve']] == [r['date'] for r in expected['curve']]
        np.testing.assert_allclose([r['equity'] for r in actual['curve']], [r['equity'] for r in expected['curve']], atol=1e-7)
        assert actual['trade_count'] == expected['trade_count']
        write(directory/f'production-{key}.json', actual)
        counts[key] = len(actual['curve'])
        print(f'PRODUCTION {key}: {counts[key]} points', flush=True)
    write(directory/'production-parity.json', {'passed': True, 'curves': counts, 'user_settings_changed': False})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.verify:
        verify_production(args.directory.resolve())
    else:
        audit(args.directory.resolve())
