"""Persist file-derived Prop evidence in the manager runtime directory."""

from __future__ import annotations

import json
import os
import sqlite3
import zlib
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from portfolio_manager.ubs_portfolio import ClosedTrade, PeriodReport
from portfolio_manager.ubs_portfolio.reports import period_report_from_strategy_report
from ubs.path_utils import resolve_workspace_path

from .prop_experiment_engine import set_path_is_grid
from .prop_experiment_pool import _ohlc_report


def _decode_period(data) -> PeriodReport:
    for trade in data['closed_trades']:
        for field in ('open_time', 'close_time'):
            if trade[field] is not None:
                trade[field] = datetime.fromisoformat(trade[field])
    data['closed_trades'] = [ClosedTrade(**trade) for trade in data['closed_trades']]
    data['pnl_points_001'] = [(datetime.fromisoformat(day), pnl) for day, pnl in data['pnl_points_001']]
    return PeriodReport(**data)


class PropEvidenceCache:
    def __init__(self, path: Path, *, refresh: bool = False):
        self.path = path
        self.refresh = refresh
        self.connection = None
        self.error = None
        self.hits = self.misses = 0
        self.refreshed = set()

    def __enter__(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path)
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS evidence (
                    kind TEXT, path TEXT, mtime INTEGER, size INTEGER, payload BLOB,
                    PRIMARY KEY (kind, path)
                )
            ''')
        except (OSError, sqlite3.Error) as exc:
            self._disable(exc)
        return self

    def __exit__(self, exc_type, *_details):
        if self.connection is not None:
            try:
                if exc_type is None:
                    self.connection.commit()
            except sqlite3.Error as exc:
                self.error = str(exc)
            finally:
                self.connection.close()
                self.connection = None

    def _disable(self, exc):
        self.error = str(exc)
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _read(self, kind, path, load, encode, decode):
        if self.connection is None or not path.is_file():
            return load(path)
        stat = path.stat()
        key = kind, os.path.normcase(str(path.absolute()))
        signature = stat.st_mtime_ns, stat.st_size
        try:
            if self.refresh and key not in self.refreshed:
                self.connection.execute('DELETE FROM evidence WHERE kind = ? AND path = ?', key)
            else:
                cached = self.connection.execute(
                    'SELECT payload FROM evidence WHERE kind = ? AND path = ? AND mtime = ? AND size = ?',
                    (*key, *signature),
                ).fetchone()
                if cached is not None:
                    value = decode(json.loads(zlib.decompress(cached[0])))
                    self.hits += 1
                    return value
        except (ValueError, KeyError, TypeError, zlib.error):
            pass
        except sqlite3.Error as exc:
            self._disable(exc)
        value = load(path)
        self.misses += 1
        if self.connection is not None:
            try:
                payload = zlib.compress(json.dumps(encode(value), default=datetime.isoformat).encode(), level=1)
                self.connection.execute('INSERT OR REPLACE INTO evidence VALUES (?, ?, ?, ?, ?)',
                                        (*key, *signature, payload))
                self.refreshed.add(key)
            except sqlite3.Error as exc:
                self._disable(exc)
        return value

    def read_period(self, path: Path) -> PeriodReport:
        return self._read('ohlc-v1', path, _load_period, asdict, _decode_period)

    def is_grid(self, path: str) -> bool:
        resolved = resolve_workspace_path(path)
        return self._read('grid-v1', resolved, lambda file: set_path_is_grid(file, fresh=True), int, bool)


def _load_period(path: Path) -> PeriodReport:
    return period_report_from_strategy_report(_ohlc_report(path), 'OHLC')
