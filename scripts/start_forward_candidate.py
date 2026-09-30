"""Register the already-researched 2x candidate for paper observation, not live execution."""
import argparse
import json
from pathlib import Path

from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.observation import KEY, control, enqueue_observation
from ai_stock_bot.v4.schema import Plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, default=Path('docs/reports/daily-4-7-stability/results.json'))
    args = parser.parse_args()
    store = Store(args.data_dir)
    if store.meta(KEY):
        raise SystemExit('An observation already exists; it will not be overwritten.')
    evidence = json.loads(args.report.read_text(encoding='utf-8'))
    plan = Plan.model_validate({**evidence['later']['2']['plan'],
                               'name': '研究候选 · 2x 多周期动态仓位', 'auto_update': False})
    if plan.research_multiple != 2 or plan.method != 'rotation' or plan.research_interval != 'daily':
        raise SystemExit('Unexpected research candidate; refusing to start.')
    before_plan, before_account = store.meta('investment_settings'), store.meta('portfolio_state')
    study = control(store, 'start', plan)
    job = enqueue_observation(store, manual=True)
    assert store.meta('investment_settings') == before_plan
    assert store.meta('portfolio_state') == before_account
    print(json.dumps({'study_id': study['id'], 'job_id': job['id'],
                      'paper_only': True, 'main_plan_unchanged': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
