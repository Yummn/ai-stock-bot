"""Bounded defensive refinement with a locked candidate and unchanged risk budgets."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import sys

from research_balanced_preset import sha
from research_core_profiles import checks
from research_resilient_execution import ROOT, SOURCE, SNAPSHOT, load, write, worker


def protocol():
    from ai_stock_bot.v4.schema import FIXED_PROFILE_PRESETS, Plan
    baseline = Plan(**{k: v for k, v in FIXED_PROFILE_PRESETS['conservative'].items() if k != 'label'},
                    profile='custom', preset_version=1).model_dump()
    changes = {
        'anchor-zero': {'core_anchor': 0.},
        'anchor-lighter': {'core_anchor': .1},
        'anchor-stronger': {'core_anchor': .4},
        'risk-penalty-lighter': {'risk_aversion': 1.5},
        'risk-penalty-stronger': {'risk_aversion': 6.},
        'drift-2': {'drift_band': .02},
        'drift-6': {'drift_band': .06},
        'drift-8': {'drift_band': .08},
        'weekly-rebalance': {'rebalance': 'weekly'},
        'weekly-exit-review': {'risk_review': 'weekly'},
        'daily-exit-review': {'risk_review': 'daily'},
        'trend-consensus': {'signal_policy': 'consensus'},
        'fast-confirmation': {'signal_policy': 'fast_confirmed'},
    }
    previous = load(ROOT/'docs/reports/core-profiles-20260930/protocol.json')
    return {
        'version': 'conservative-refinement-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
        'market_sha256': sha(SNAPSHOT), 'forecast_sha256': sha(SOURCE),
        'cases': {'original': baseline, **{k: {**baseline, **v} for k, v in changes.items()}},
        'periods': previous['periods'], 'limits': previous['gates']['conservative'],
        'baseline_reference': 'docs/reports/profile-algorithms-20260930/cases/full-conservative--hgb20_regularized-base.result.json',
        'selection': 'Development 2020-2022 only. Prefer normal DD <=6% and worst calendar year >=-3%; rank stressed net CAGR minus absolute drawdown plus half worst negative calendar year. Include original and prefer it on ties. Lock one before later/full/recent/independent-year checks. No replacement winner after validation fails.',
        'promotion': 'All original defensive gates pass, full net CAGR improves >=0.25pp, later CAGR >=original, full double-cost CAGR >original, recent independent return no worse by >0.25pp, full DD no worse by >0.5pp. No increase to volatility budget, position caps, leverage, or loss limits.',
        'limitations': ['All periods were researched before; none is a pristine holdout.',
            'Limited one-factor search, not an exhaustive optimum.',
            'Fixed six ETF universe; no fictional cash interest or new historical assets.',
            'Historical passing cannot demonstrate future profits.'],
        'references': ['https://www.cvxportfolio.com/en/stable/manual.html',
            'https://scikit-learn.org/stable/auto_examples/model_selection/plot_nested_cross_validation_iris.html'],
    }


def selection_score(pair):
    base, stress = pair['base'], pair['stress']
    eligible = base['max_drawdown'] >= -.06 and min(r['return_pct'] for r in base['per_year']) >= -.03
    score = stress['liquidation_cagr'] - abs(stress['max_drawdown']) + .5*min(0., min(r['return_pct'] for r in stress['per_year']))
    return eligible, score


def promotion_checks(new, old, historical_passed):
    return {
        'historical_gates': historical_passed,
        'full_growth': new['full']['base']['liquidation_cagr'] >= old['full']['base']['liquidation_cagr']+.0025,
        'later_growth': new['later']['base']['liquidation_cagr'] >= old['later']['base']['liquidation_cagr'],
        'stressed_growth': new['full']['stress']['liquidation_cagr'] > old['full']['stress']['liquidation_cagr'],
        'recent_loss': new['recent']['base']['liquidation_return'] >= old['recent']['base']['liquidation_return']-.0025,
        'drawdown_change': new['full']['base']['max_drawdown'] >= old['full']['base']['max_drawdown']-.005,
    }


def main(directory):
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory/'protocol.json').exists():
        spec = protocol()
        files = [Path(__file__).resolve(), ROOT/'scripts/research_resilient_execution.py',
                 ROOT/'scripts/research_core_profiles.py', *sorted((ROOT/'src/ai_stock_bot/v4').glob('*.py'))]
        spec['source_sha256'] = {str(p.relative_to(ROOT)).replace('\\', '/'): sha(p) for p in files}
        for p in files:
            dest = directory/'source'/p.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
        write(directory/'protocol.json', spec)
    spec = load(directory/'protocol.json')
    for name, digest in spec['source_sha256'].items():
        assert sha(ROOT/name) == digest, f'Source changed: {name}'
    path = directory/'results.json'
    result = load(path) if path.exists() else {'development': {}, 'finalists': {}, 'independent_years': {}}

    def run(key, period, both=True):
        pair = {}
        for stress in ((False, True) if both else (False,)):
            name = f"{period}-{key}-{'stress' if stress else 'base'}"
            case = directory/'cases'/f'{name}.json'
            request = {'plan': spec['cases'][key], 'period': spec['periods'][period], 'stress': stress}
            if case.exists():
                assert load(case) == request
            else:
                write(case, request)
            target = case.with_suffix('.result.json')
            if not target.exists():
                (directory/'logs').mkdir(exist_ok=True)
                with (directory/'logs'/f'{name}.log').open('w', encoding='utf-8') as stream:
                    completed = subprocess.run([sys.executable, '-X', 'faulthandler', __file__, '--output', str(directory), '--worker', str(case)],
                        cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT/'src'), 'PYTHONUTF8': '1',
                            'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2', 'OPENBLAS_NUM_THREADS': '2'},
                        stdout=stream, stderr=subprocess.STDOUT)
                if completed.returncode:
                    raise RuntimeError(f'Replay failed: {name}, exit {completed.returncode}')
            raw = load(target)
            pair['stress' if stress else 'base'] = {k: v for k, v in raw.items()
                if k not in {'curve', 'trades', 'decisions', 'training', 'rejections'}}
        print(f"{period} {key}: CAGR {pair['base']['liquidation_cagr']:.3%}; DD {pair['base']['max_drawdown']:.3%}; trades {pair['base']['trade_count']}", flush=True)
        return pair

    for key in spec['cases']:
        if key not in result['development']:
            result['development'][key] = run(key, 'development')
            write(path, result)
    winner = max(spec['cases'], key=lambda k: (*selection_score(result['development'][k]), k == 'original'))
    if result.get('locked'):
        assert result['locked'] == winner
    result.update(locked=winner, locked_at=result.get('locked_at', datetime.now(timezone.utc).isoformat()),
                  scores={k: selection_score(v) for k, v in result['development'].items()})
    write(path, result)
    print(f'LOCKED {winner}', flush=True)
    for key in dict.fromkeys(['original', winner]):
        periods = result['finalists'].setdefault(key, {})
        for period in ('later', 'recent', 'full'):
            if period not in periods:
                periods[period] = run(key, period)
                write(path, result)
        years = result['independent_years'].setdefault(key, {})
        for year in map(str, range(2020, 2026)):
            if year not in years:
                years[year] = run(key, year, both=False)
                write(path, result)
    verdict = checks(result['finalists'][winner], result['independent_years'][winner], spec['limits'])
    promoted = promotion_checks(result['finalists'][winner], result['finalists']['original'], all(verdict.values()))
    result.update(historical_checks=verdict, promotion=promoted, promoted=all(promoted.values()),
                  forward_proven=False, completed_at=datetime.now(timezone.utc).isoformat(), applied=False)
    write(path, result)
    print(f"PROMOTION {result['promoted']} {promoted}", flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker.resolve(), args.output.resolve())
    else:
        main(args.output.resolve())
