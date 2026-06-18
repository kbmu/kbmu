"""Smoke tests for trading_bot.

These mock the ccxt exchange so no network calls or real orders are made.
Run with:  pytest
"""

import os
import sys
from unittest.mock import MagicMock

import pandas as pd
import pytest

# Ensure the bot is importable and runs in dry-run during import.
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("COINBASE_API_KEY", "test-key")
os.environ.setdefault("COINBASE_API_SECRET", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trading_bot as bot  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

def make_indicators(**overrides):
    """Build an Indicators bundle with sensible neutral defaults."""
    base = dict(
        rsi=50.0,
        sma=100.0,
        macd=0.0,
        macd_signal=0.0,
        bb_upper=110.0,
        bb_lower=90.0,
        trend=100.0,
    )
    base.update(overrides)
    return bot.Indicators(**base)


def buy_signal_indicators():
    # rsi < oversold, price > sma, macd > signal, price < bb_lower, price > trend
    return make_indicators(rsi=20.0, sma=80.0, macd=1.0, macd_signal=0.0,
                           bb_lower=200.0, trend=50.0)


def sell_signal_indicators():
    # rsi > overbought, price < sma, macd < signal, price > bb_upper, price < trend
    return make_indicators(rsi=80.0, sma=200.0, macd=0.0, macd_signal=1.0,
                           bb_upper=50.0, trend=200.0)


# --------------------------------------------------------------------------- #
# Strategy condition tests
# --------------------------------------------------------------------------- #

def test_should_buy_true_when_all_conditions_met():
    assert bot.should_buy(buy_signal_indicators(), price=100.0) is True


def test_should_buy_false_when_rsi_not_oversold():
    ind = buy_signal_indicators()._replace(rsi=50.0)
    assert bot.should_buy(ind, price=100.0) is False


def test_should_sell_true_when_all_conditions_met():
    assert bot.should_sell(sell_signal_indicators(), price=100.0) is True


def test_should_sell_false_when_rsi_not_overbought():
    ind = sell_signal_indicators()._replace(rsi=50.0)
    assert bot.should_sell(ind, price=100.0) is False


# --------------------------------------------------------------------------- #
# Position sizing
# --------------------------------------------------------------------------- #

def test_position_size_for_basic():
    # balance 1000 * risk 0.01 = 10 risk; / price 2 = 5 units
    bot.parameters["risk_percentage"] = 0.01
    assert bot.position_size_for(1000.0, 2.0) == pytest.approx(5.0)


def test_position_size_for_zero_price():
    assert bot.position_size_for(1000.0, 0.0) == 0.0


# --------------------------------------------------------------------------- #
# Protective levels & evaluate() state machine
# --------------------------------------------------------------------------- #

def test_protective_levels():
    bot.parameters["stop_loss_percentage"] = 0.02
    bot.parameters["take_profit_percentage"] = 0.05
    sl, tp = bot.protective_levels(100.0)
    assert sl == pytest.approx(98.0)
    assert tp == pytest.approx(105.0)


def test_evaluate_opens_on_buy_signal_when_flat():
    action, reason = bot.evaluate(None, buy_signal_indicators(), price=100.0)
    assert action == "open"
    assert reason == "buy_signal"


def test_evaluate_holds_when_flat_and_no_signal():
    action, _ = bot.evaluate(None, make_indicators(), price=100.0)
    assert action == "hold"


def test_evaluate_closes_on_stop_loss():
    pos = bot.Position(size=5.0, entry_price=100.0, stop_loss=98.0, take_profit=105.0)
    action, reason = bot.evaluate(pos, make_indicators(), price=97.0)
    assert action == "close"
    assert reason == "stop_loss"


def test_evaluate_closes_on_take_profit():
    pos = bot.Position(size=5.0, entry_price=100.0, stop_loss=98.0, take_profit=105.0)
    action, reason = bot.evaluate(pos, make_indicators(), price=106.0)
    assert action == "close"
    assert reason == "take_profit"


def test_evaluate_closes_on_sell_signal():
    pos = bot.Position(size=5.0, entry_price=100.0, stop_loss=98.0, take_profit=105.0)
    action, reason = bot.evaluate(pos, sell_signal_indicators(), price=100.0)
    assert action == "close"
    assert reason == "sell_signal"


def test_evaluate_stop_loss_takes_priority_over_sell_signal():
    pos = bot.Position(size=5.0, entry_price=100.0, stop_loss=98.0, take_profit=105.0)
    action, reason = bot.evaluate(pos, sell_signal_indicators(), price=97.0)
    assert reason == "stop_loss"


def test_evaluate_holds_position_when_price_between_levels():
    pos = bot.Position(size=5.0, entry_price=100.0, stop_loss=98.0, take_profit=105.0)
    action, _ = bot.evaluate(pos, make_indicators(), price=100.0)
    assert action == "hold"


# --------------------------------------------------------------------------- #
# Order placement (DRY_RUN must never touch the exchange)
# --------------------------------------------------------------------------- #

def test_place_order_dry_run_does_not_call_exchange(monkeypatch):
    monkeypatch.setattr(bot, "DRY_RUN", True)
    exchange = MagicMock()
    order = bot.place_order(exchange, "buy", 5.0)
    assert order["dry_run"] is True
    exchange.create_market_buy_order.assert_not_called()


def test_place_order_live_calls_exchange(monkeypatch):
    monkeypatch.setattr(bot, "DRY_RUN", False)
    monkeypatch.setattr(bot, "send_email", lambda *a, **k: None)
    exchange = MagicMock()
    exchange.create_market_buy_order.return_value = {"id": "abc"}
    order = bot.place_order(exchange, "buy", 5.0)
    exchange.create_market_buy_order.assert_called_once_with(bot.SYMBOL, 5.0)
    assert order == {"id": "abc"}


def test_place_order_rejects_nonpositive_amount():
    exchange = MagicMock()
    assert bot.place_order(exchange, "buy", 0.0) is None
    exchange.create_market_buy_order.assert_not_called()


# --------------------------------------------------------------------------- #
# run_once integration with a mocked exchange
# --------------------------------------------------------------------------- #

def _candles(price, n=250):
    rows = [[i, price, price, price, price, 100.0] for i in range(n)]
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def test_run_once_opens_position_on_buy(monkeypatch):
    monkeypatch.setattr(bot, "DRY_RUN", True)
    monkeypatch.setattr(bot, "get_market_price", lambda ex: 100.0)
    monkeypatch.setattr(bot, "get_balance", lambda ex: 1000.0)
    monkeypatch.setattr(bot, "fetch_historical_data", lambda ex: _candles(100.0))
    monkeypatch.setattr(bot, "calculate_indicators", lambda data: buy_signal_indicators())

    position = bot.run_once(MagicMock(), None)
    assert position is not None
    assert position.size > 0
    assert position.stop_loss < position.entry_price < position.take_profit


def test_run_once_closes_position_on_stop_loss(monkeypatch):
    monkeypatch.setattr(bot, "DRY_RUN", True)
    monkeypatch.setattr(bot, "get_market_price", lambda ex: 95.0)  # below stop loss
    monkeypatch.setattr(bot, "fetch_historical_data", lambda ex: _candles(95.0))
    monkeypatch.setattr(bot, "calculate_indicators", lambda data: make_indicators())

    pos = bot.Position(size=5.0, entry_price=100.0, stop_loss=98.0, take_profit=105.0)
    position = bot.run_once(MagicMock(), pos)
    assert position is None  # closed


def test_run_once_holds_when_no_signal(monkeypatch):
    monkeypatch.setattr(bot, "DRY_RUN", True)
    monkeypatch.setattr(bot, "get_market_price", lambda ex: 100.0)
    monkeypatch.setattr(bot, "fetch_historical_data", lambda ex: _candles(100.0))
    monkeypatch.setattr(bot, "calculate_indicators", lambda data: make_indicators())

    position = bot.run_once(MagicMock(), None)
    assert position is None


# --------------------------------------------------------------------------- #
# Indicators / backtest smoke tests (real ta computation, no network)
# --------------------------------------------------------------------------- #

def test_calculate_indicators_returns_bundle():
    data = _candles(100.0)
    data.loc[:, "close"] = [100.0 + (i % 10) for i in range(len(data))]
    ind = bot.calculate_indicators(data)
    assert isinstance(ind, bot.Indicators)
    assert ind.rsi is not None


def test_backtest_runs_without_network():
    data = _candles(100.0)
    data.loc[:, "close"] = [100.0 + (i % 7) for i in range(len(data))]
    # Should complete without raising and without calling any exchange.
    bot.backtest_strategy(data)
