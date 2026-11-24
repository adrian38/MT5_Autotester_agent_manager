"""Audit the actual Prop search bases on their common OHLC calendar."""

from __future__ import annotations

from portfolio_manager.ubs_portfolio import pearson_correlation


def search_base_payload(optimizer, strategies, axis):
    required = dict.fromkeys(sorted(optimizer.required), 1)
    rows = []
    series = []
    for key, units in optimizer.seed_leaders:
        item = strategies[key]
        evaluation = optimizer.measure({**required, key: units})
        rows.append({
            'key': key, 'set_path': item.lab.set_path, 'set_id': item.lab.set_id,
            'broker': item.lab.origin, 'symbol': item.lab.symbol, 'timeframe': item.lab.timeframe,
            'lot': round(units * item.lot_per_unit, 8), 'passed': evaluation.passed,
            'median_days': evaluation.median_days if evaluation.passed else None,
        })
        series.append(item.series)
    return {
        'strategies': rows, 'days': axis.size, 'required_count': len(required),
        'correlation': _correlation_matrix(series),
    }


def _correlation_matrix(series):
    varying = [len(set(values)) > 1 for values in series]
    matrix = [[None] * len(series) for _ in series]
    for row, left in enumerate(series):
        for column in range(row + 1):
            right = series[column]
            if varying[row] and varying[column] and len(left) == len(right):
                coefficient = pearson_correlation(left, right)
                value = round(max(-1.0, min(1.0, coefficient)), 6)
                matrix[row][column] = matrix[column][row] = value
    return matrix
