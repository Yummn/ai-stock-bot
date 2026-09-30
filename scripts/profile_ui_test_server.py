"""Isolated browser test harness. Never uses the user's investment database."""
from pathlib import Path

import uvicorn

from ai_stock_bot.v2.api import create_app
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.schema import FIXED_PROFILE_PRESETS, Plan
from ai_stock_bot.v4.service import save_settings

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT/'docs/reports/profile-algorithms-20260930'

if __name__ == '__main__':
    store = Store(DIRECTORY/'ui-store')
    if not store.meta('investment_settings'):
        plan = Plan(**{k: v for k, v in FIXED_PROFILE_PRESETS['aggressive'].items() if k != 'label'},
                    profile='aggressive', preset_version=1)
        save_settings(store, plan, 1, 'aggressive')
    summary = DIRECTORY/'summary.json'
    if summary.exists():
        import json
        store.set_meta('profile_algorithm_study', json.loads(summary.read_text(encoding='utf-8')))
    uvicorn.run(create_app(store.root, static_root=DIRECTORY/'ui-dist', start_manager=False), host='127.0.0.1', port=8773)
