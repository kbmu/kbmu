"""Paper-money simulation harness.

Runs the bot's backtest over synthetic random-walk price series (no network, no
real or fake account needed) and reports the resulting P&L. Useful as a smoke
test that the strategy actually trades and that fees/slippage are applied.

NOTE: a random walk has no predictable edge, so the expected outcome is roughly
break-even minus fees. This validates that the bot *functions*, not that the
strategy is profitable — that requires real historical data.

Usage:
    python scripts/paper_sim.py [--runs N] [--candles N] [--sigma F]
"""

import argparse
import logging
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trading_bot as bot  # noqa: E402


def synth_candles(n, seed, sigma, start=0.5):
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(0.0, sigma, n)))
    return pd.DataFrame(
        {
            "timestamp": np.arange(n),
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": 1000.0,
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10, help="number of random seeds")
    parser.add_argument("--candles", type=int, default=3000, help="candles per run")
    parser.add_argument("--sigma", type=float, default=0.0008, help="per-step volatility")
    args = parser.parse_args()

    logging.disable(logging.CRITICAL)  # keep the report clean

    print("Paper-money simulation (synthetic random walks; no network)")
    print(
        f"start=$1000.00  fee_rate={bot.parameters['fee_rate']}  "
        f"slippage={bot.parameters['slippage']}  runs={args.runs}  candles={args.candles}"
    )
    print("-" * 64)

    finals = []
    for seed in range(args.runs):
        final = bot.backtest_strategy(synth_candles(args.candles, seed, args.sigma))
        if final is None:
            print(f"seed {seed:>2}: not enough data")
            continue
        finals.append(final)
        print(f"seed {seed:>2}: final = ${final:>9.2f}  ({(final / 1000 - 1) * 100:+.2f}%)")

    if finals:
        arr = np.array(finals)
        print("-" * 64)
        print(
            f"mean ${arr.mean():.2f}  min ${arr.min():.2f}  max ${arr.max():.2f}  "
            f"mean return {(arr.mean() / 1000 - 1) * 100:+.2f}%"
        )


if __name__ == "__main__":
    main()
