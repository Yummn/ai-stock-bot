"""Prepare an isolated UI-validation copy without racing the running job manager."""
from contextlib import closing
import argparse
from pathlib import Path
import shutil
import sqlite3

from ai_stock_bot.v2.store import Store

parser = argparse.ArgumentParser()
parser.add_argument("--target", default="data/v3-upgrade-preview")
args = parser.parse_args()
source = Store(Path("data/v3"))
target = Path(args.target)
target.mkdir(exist_ok=True)
if not (target / "stocklab.sqlite3").exists():
    with source.connect() as connection, closing(sqlite3.connect(target / "stocklab.sqlite3")) as destination:
        connection.backup(destination)
    for folder in ("evaluations", "learning_checks"):
        if (source.root / folder).exists():
            shutil.copytree(source.root / folder, target / folder, dirs_exist_ok=True)
print(target.resolve())
