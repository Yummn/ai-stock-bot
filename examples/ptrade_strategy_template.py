"""PTrade strategy template for ai_stock_bot.

Copy this file into PTrade's strategy editor after making ai_stock_bot importable
in that Python environment. Keep BROKER=ptrade and DRY_RUN=false only after you
have tested it in a simulation account.
"""

import builtins

from ai_stock_bot.config import load_settings
from ai_stock_bot.engine import TradingEngine


def initialize(context):
    builtins.context = context
    g.engine = TradingEngine(load_settings())
    for symbol in g.engine.settings.symbols:
        set_universe(symbol)


def handle_data(context, data):
    builtins.context = context
    g.engine.run_once()
