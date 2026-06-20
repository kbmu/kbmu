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
    # Oversold dip in an uptrend (evaluated at price 100.0):
    # rsi < oversold, price < bb_lower, price > trend, sma > trend.
    return make_indicators(rsi=20.0, sma=110.0, bb_lower=105.0, trend=95.0)


def sell_signal_indicators():
    # Overbought exhaustion (evaluated at price 100.0):
    # rsi > overbought, price > bb_upper, macd < macd_signal.
    return make_indicators(rsi=80.0, bb_upper=95.0, macd=0.0, macd_signal=1.0)


# --------------------------------------------------------------------------- #
# Strategy condition tests
# --------------------------------------------------------------------------- #

def test_should_buy_true_when_all_conditions_met():
    assert bot.should_buy(buy_signal_indicators(), price=100.0) is True


def test_should_buy_false_when_rsi_not_oversold():
    ind = buy_signal_indicators()._replace(rsi=50.0)
    assert bot.should_buy(ind, price=100.0) is False


def test_should_buy_triggers_on_dip_below_short_sma():
    # Regression: the old rule required price > sma AND price < bb_lower at once
    # (contradictory), so it never fired. A dip below the short SMA must buy.
    ind = make_indicators(rsi=20.0, sma=110.0, bb_lower=105.0, trend=95.0)
    assert ind.bb_lower < ind.sma  # price below band is also below the short SMA
    assert bot.should_buy(ind, price=100.0) is True


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


# --------------------------------------------------------------------------- #
# Exchange-managed exits in evaluate()
# --------------------------------------------------------------------------- #

def test_evaluate_skips_stop_loss_when_exchange_managed():
    pos = bot.Position(size=5.0, entry_price=100.0, stop_loss=98.0, take_profit=105.0)
    # Price below stop loss, but the exchange owns the exit: should hold here.
    action, _ = bot.evaluate(pos, make_indicators(), price=97.0, exchange_managed_exits=True)
    assert action == "hold"


def test_evaluate_still_closes_on_sell_signal_when_exchange_managed():
    pos = bot.Position(size=5.0, entry_price=100.0, stop_loss=98.0, take_profit=105.0)
    action, reason = bot.evaluate(
        pos, sell_signal_indicators(), price=100.0, exchange_managed_exits=True
    )
    assert action == "close"
    assert reason == "sell_signal"


# --------------------------------------------------------------------------- #
# Exchange-side protective (bracket) orders
# --------------------------------------------------------------------------- #

def test_place_protective_orders_dry_run_places_nothing(monkeypatch):
    monkeypatch.setattr(bot, "DRY_RUN", True)
    exchange = MagicMock()
    assert bot.place_protective_orders(exchange, 5.0, 98.0, 105.0) == []
    exchange.create_order.assert_not_called()


def test_place_protective_orders_live_places_bracket(monkeypatch):
    monkeypatch.setattr(bot, "DRY_RUN", False)
    exchange = MagicMock()
    exchange.create_order.return_value = {"id": "bracket-1"}
    ids = bot.place_protective_orders(exchange, 5.0, 98.0, 105.0)
    assert ids == ["bracket-1"]
    exchange.create_order.assert_called_once_with(
        bot.SYMBOL, "limit", "sell", 5.0, 105.0, {"stopLossPrice": 98.0}
    )


def test_place_protective_orders_falls_back_on_error(monkeypatch):
    monkeypatch.setattr(bot, "DRY_RUN", False)
    exchange = MagicMock()
    exchange.create_order.side_effect = RuntimeError("not supported")
    assert bot.place_protective_orders(exchange, 5.0, 98.0, 105.0) == []


def test_protective_order_filled_detects_closed(monkeypatch):
    monkeypatch.setattr(bot, "DRY_RUN", False)
    exchange = MagicMock()
    exchange.fetch_order.return_value = {"status": "closed"}
    assert bot.protective_order_filled(exchange, ("bracket-1",)) is True


def test_protective_order_filled_returns_none_on_error(monkeypatch):
    monkeypatch.setattr(bot, "DRY_RUN", False)
    exchange = MagicMock()
    exchange.fetch_order.side_effect = RuntimeError("network")
    assert bot.protective_order_filled(exchange, ("bracket-1",)) is None


def test_cancel_protective_orders_calls_exchange(monkeypatch):
    monkeypatch.setattr(bot, "DRY_RUN", False)
    exchange = MagicMock()
    bot.cancel_protective_orders(exchange, ("bracket-1",))
    exchange.cancel_order.assert_called_once_with("bracket-1", bot.SYMBOL)


def test_run_once_reconciles_filled_bracket(monkeypatch):
    monkeypatch.setattr(bot, "DRY_RUN", False)
    monkeypatch.setattr(bot, "USE_EXCHANGE_PROTECTIVE_ORDERS", True)
    monkeypatch.setattr(bot, "get_market_price", lambda ex: 100.0)
    exchange = MagicMock()
    exchange.fetch_order.return_value = {"status": "closed"}
    pos = bot.Position(5.0, 100.0, 98.0, 105.0, ("bracket-1",))
    # Should detect the fill and report flat without re-selling.
    assert bot.run_once(exchange, pos) is None
    exchange.create_market_sell_order.assert_not_called()


def test_run_once_open_places_bracket_when_enabled(monkeypatch):
    monkeypatch.setattr(bot, "DRY_RUN", False)
    monkeypatch.setattr(bot, "USE_EXCHANGE_PROTECTIVE_ORDERS", True)
    monkeypatch.setattr(bot, "send_email", lambda *a, **k: None)
    monkeypatch.setattr(bot, "get_market_price", lambda ex: 100.0)
    monkeypatch.setattr(bot, "get_balance", lambda ex: 1000.0)
    monkeypatch.setattr(bot, "fetch_historical_data", lambda ex: _candles(100.0))
    monkeypatch.setattr(bot, "calculate_indicators", lambda data: buy_signal_indicators())
    exchange = MagicMock()
    exchange.create_market_buy_order.return_value = {"id": "buy-1"}
    exchange.create_order.return_value = {"id": "bracket-1"}

    position = bot.run_once(exchange, None)
    assert position is not None
    assert position.protective_order_ids == ("bracket-1",)
    exchange.create_order.assert_called_once()


# --------------------------------------------------------------------------- #
# Position persistence
# --------------------------------------------------------------------------- #

def test_save_and_load_position_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    pos = bot.Position(5.0, 100.0, 98.0, 105.0, ("bracket-1",))
    bot.save_position(pos, path=path)
    loaded = bot.load_position(path=path)
    assert loaded == pos
    assert loaded.protective_order_ids == ("bracket-1",)


def test_save_none_removes_state_file(tmp_path):
    path = str(tmp_path / "state.json")
    bot.save_position(bot.Position(5.0, 100.0, 98.0, 105.0), path=path)
    assert os.path.exists(path)
    bot.save_position(None, path=path)
    assert not os.path.exists(path)


def test_load_position_missing_file_returns_none(tmp_path):
    assert bot.load_position(path=str(tmp_path / "nope.json")) is None


# --------------------------------------------------------------------------- #
# Backtest fees & slippage
# --------------------------------------------------------------------------- #

def test_fill_prices_apply_slippage(monkeypatch):
    bot.parameters["slippage"] = 0.001
    assert bot.buy_fill_price(100.0) == pytest.approx(100.1)   # worse for buyer
    assert bot.sell_fill_price(100.0) == pytest.approx(99.9)   # worse for seller


def test_trade_fee_is_fraction_of_notional():
    bot.parameters["fee_rate"] = 0.006
    assert bot.trade_fee(1000.0) == pytest.approx(6.0)


def test_backtest_round_trip_loses_to_fees_and_slippage(monkeypatch):
    # Force a buy then a sell at a flat price; with fees+slippage the final
    # balance must be below the starting balance.
    monkeypatch.setitem(bot.parameters, "fee_rate", 0.006)
    monkeypatch.setitem(bot.parameters, "slippage", 0.001)

    signals = iter([buy_signal_indicators(), sell_signal_indicators()])

    def fake_indicators(_window):
        try:
            return next(signals)
        except StopIteration:
            return make_indicators()

    monkeypatch.setattr(bot, "calculate_indicators", fake_indicators)

    final_balance = bot.backtest_strategy(_candles(100.0, n=205))
    assert final_balance is not None
    assert final_balance < 1000.0


def test_backtest_zero_fees_zero_slippage_flat_market_breaks_even(monkeypatch):
    monkeypatch.setitem(bot.parameters, "fee_rate", 0.0)
    monkeypatch.setitem(bot.parameters, "slippage", 0.0)

    signals = iter([buy_signal_indicators(), sell_signal_indicators()])

    def fake_indicators(_window):
        try:
            return next(signals)
        except StopIteration:
            return make_indicators()

    monkeypatch.setattr(bot, "calculate_indicators", fake_indicators)

    final_balance = bot.backtest_strategy(_candles(100.0, n=205))
    assert final_balance == pytest.approx(1000.0)
