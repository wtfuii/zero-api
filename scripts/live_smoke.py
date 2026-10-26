#!/usr/bin/env python3
"""Live smoke test for zero-api, including an opt-in real-money order round-trip.

This script talks to the real Finanzen.net ZERO broker. It is a developer tool,
not part of the offline test suite (``tests/`` never touches the network).

Read-only checks always run:

* login + ``account.get``
* ``instrument.search`` by ISIN, WKN and name
* ``instrument.get``, ``instrument.detail``, ``instrument.chart``
* ``portfolio.get_positions``
* ``watchlist.get``
* ``watchlist.add_to_watchlist`` + ``remove_from_watchlist`` (round-trip,
  restored afterwards; disable with ``--skip-mutations``)
* ``orders.list`` for the Open, Canceled and Filled tabs
* ``sessiontan.is_authorized`` + ``sessiontan.enrolled_devices`` (state probes)

Opt-in live trade (real money, requires app-push 2FA), enabled by
``--trade-isin``:

* unlock order execution; when the session is locked and several devices are
  enrolled, the script asks which device should receive the push
* ``orders.place`` a market BUY for exactly 1 unit
* poll the Open/Canceled/Filled order lists until that buy is confirmed filled
* only then ``orders.place`` a market SELL for exactly 1 unit
* ``orders.cancel`` is used solely as cleanup if an order does not fill in time

The buy is never followed by a sell unless the buy fill is confirmed in the
broker's order lists. Fees apply to both orders; this is not a free test.

Usage::

    ZERO_EMAIL=you@example.com ZERO_PASSWORD=secret \\
        uv run python scripts/live_smoke.py

    ZERO_EMAIL=you@example.com ZERO_PASSWORD=secret \\
        uv run python scripts/live_smoke.py --trade-isin <ISIN>

If ``ZERO_PASSWORD`` is unset and the process has a TTY, the script prompts for
it instead of taking it on the command line. Exit code 0 means every executed
check passed; 1 means a failure (or an unclean watchlist cleanup); 2 means a
usage/configuration error.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import partial
from time import monotonic, perf_counter, sleep
from typing import Literal, NoReturn
from zoneinfo import ZoneInfo

from zero_api import ZeroClient
from zero_api.core.exceptions import AuthenticationError
from zero_api.core.http import BASE_URL
from zero_api.instrument.models import Instrument, TradingVenue
from zero_api.ordering.models import ExecType, Order, OrderRequest
from zero_api.sessiontan.models import EnrolledDevice

DEFAULT_ISIN = "DE0007030009"  # Rheinmetall AG, the share used in explorations
# Wire values of the order-history tabs in the SPA (main bundle, 2026-10-26):
# "canceledOrders" -> status=Canceled, "filledOrders" -> status=Filled.
ORDER_STATUSES = ("Open", "Canceled", "Filled")
# Order statuses that mean the order reached a final filled state.
FILLED_STATUSES = frozenset({"Filled", "PartiallyFilled_Filled"})
# Order statuses that mean the order is final without (fully) filling.
FINAL_UNFILLED_STATUSES = frozenset(
    {"Canceled", "Rejected", "Expired", "PartiallyFilled_Canceled", "PartiallyFilled_Expired"}
)
TRADE_QUANTITY = Decimal("1")
TRADE_TIMEZONE = ZoneInfo("Europe/Berlin")
# Tried in order for the watchlist round-trip. The first one not already on the
# list is used, so a normal account never has its curated list disturbed.
DEFAULT_WATCH_ISINS = (
    "DE0007030009",  # Rheinmetall AG
    "US0378331005",  # Apple Inc.
    "DE0007164600",  # SAP SE
    "IE00B4L5Y983",  # iShares Core MSCI World UCITS ETF
)

Status = Literal["PASS", "FAIL", "SKIP"]


@dataclass
class Result:
    name: str
    status: Status
    detail: str
    seconds: float


@dataclass(frozen=True)
class Config:
    email: str | None
    isin: str
    trade_isin: str | None
    watch_isin: str | None
    mic: str | None
    device: str | None
    mutations: bool
    verbose: bool
    assume_yes: bool
    fill_timeout: float
    poll_interval: float
    auth_timeout: float


@dataclass(frozen=True)
class TradePlan:
    isin: str
    name: str
    instrument_type: str
    venue_name: str
    mic_code: str
    reference_price: Decimal
    currency: str
    existing_quantity: Decimal


class _AbortSmoke(Exception):
    """Credentials are unusable; stop instead of retrying the login everywhere."""


class _SkipTrade(Exception):
    """The live trade was intentionally not started (e.g. declined by the user)."""


class _TradeError(Exception):
    """A live-trade step could not be completed safely."""


def _describe(exc: BaseException) -> str:
    text = " ".join(f"{type(exc).__name__}: {exc}".split())
    return text if len(text) <= 400 else f"{text[:397]}..."


def _format_seconds(seconds: float) -> str:
    return f"{seconds:5.2f}s"


def _resolve_credentials(email_arg: str | None) -> tuple[str, str]:
    email = email_arg or os.environ.get("ZERO_EMAIL")
    password = os.environ.get("ZERO_PASSWORD")
    if not email:
        if not sys.stdin.isatty():
            _config_error("ZERO_EMAIL is not set (or pass --email)")
        email = input("ZERO email: ").strip()
    if not password:
        if not sys.stdin.isatty():
            _config_error("ZERO_PASSWORD is not set")
        password = getpass.getpass("ZERO password: ")
    if not email or not password:
        _config_error("email and password must not be empty")
    return email, password


def _config_error(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def _minutes(hhmm: str) -> int:
    """Parse the instrument wire's 'HH:MM' / 'HH:MM:SS' trading-hours value."""
    try:
        hour, minute = hhmm.split(":")[:2]
        return int(hour) * 60 + int(minute)
    except (ValueError, IndexError) as exc:
        raise _TradeError(f"unreadable trading-hours value {hhmm!r}") from exc


class Runner:
    """Runs checks, prints one line per check and keeps the summary."""

    def __init__(self, *, verbose: bool) -> None:
        self.verbose = verbose
        self.results: list[Result] = []

    def check(
        self,
        name: str,
        action: Callable[[], str],
        *,
        fatal_on_auth: bool = False,
    ) -> None:
        started = perf_counter()
        try:
            detail = action()
        except _SkipTrade as exc:
            self._record(name, "SKIP", str(exc), perf_counter() - started)
        except Exception as exc:
            seconds = perf_counter() - started
            self._record(name, "FAIL", _describe(exc), seconds)
            if self.verbose:
                traceback.print_exc()
            if fatal_on_auth and isinstance(exc, AuthenticationError):
                raise _AbortSmoke from exc
        else:
            self._record(name, "PASS", detail, perf_counter() - started)

    def skip(self, name: str, reason: str) -> None:
        self._record(name, "SKIP", reason, 0.0)

    def _record(self, name: str, status: Status, detail: str, seconds: float) -> None:
        result = Result(name, status, detail, seconds)
        self.results.append(result)
        if status == "SKIP":
            print(f"  {status:<4}  {name:<31} - {detail}")
        else:
            print(f"  {status:<4}  {name:<31} {_format_seconds(seconds)}  {detail}")

    @property
    def failed(self) -> int:
        return sum(1 for result in self.results if result.status == "FAIL")

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.status == "PASS")

    @property
    def skipped(self) -> int:
        return sum(1 for result in self.results if result.status == "SKIP")


class SmokeTest:
    def __init__(self, client: ZeroClient, config: Config) -> None:
        self.client = client
        self.config = config
        self.runner = Runner(verbose=config.verbose)
        self.watchlist_isins: set[str] | None = None
        self.cleanup_warnings: list[str] = []
        self.trade_plan: TradePlan | None = None
        self.buy_order: Order | None = None
        self.buy_filled = False
        self.sell_order: Order | None = None

    def run(self) -> None:
        self.runner.check("account.get (+ login)", self._account, fatal_on_auth=True)
        self.runner.check("instrument.search", self._search)
        self.runner.check("instrument.get", self._instrument)
        self.runner.check("instrument.detail", self._detail)
        self.runner.check("instrument.chart", self._chart)
        self.runner.check("portfolio.get_positions", self._positions)
        self.runner.check("watchlist.get", self._watchlist_get)
        self._run_watchlist_mutation()
        for status in ORDER_STATUSES:
            self.runner.check(f"orders.list {status}", partial(self._orders, status))
        self._run_trade()
        self.runner.check("sessiontan.is_authorized", self._is_authorized)
        self.runner.check("sessiontan.enrolled_devices", self._devices)

    # -- checks ---------------------------------------------------------

    def _account(self) -> str:
        account = self.client.account.get()
        return (
            f"buying power {account.buying_power:,.2f} EUR, "
            f"transfer power {account.transfer_power:,.2f} EUR, overdrawn={account.overdrawn}"
        )

    def _search(self) -> str:
        by_isin = self.client.instrument.search(self.config.isin, size=5)
        hit = next((hit for hit in by_isin.results if hit.isin == self.config.isin), None)
        if hit is None:
            raise RuntimeError(
                f"search for {self.config.isin} returned {len(by_isin.results)} hit(s) "
                "but not the instrument itself"
            )
        by_wkn = self.client.instrument.search(hit.wkn, size=5)
        if not any(result.isin == self.config.isin for result in by_wkn.results):
            raise RuntimeError(f"search by WKN {hit.wkn} did not return {self.config.isin}")
        by_name = self.client.instrument.search(hit.short_name, size=5)
        if not by_name.results:
            raise RuntimeError(f"search by name {hit.short_name!r} returned no results")
        return (
            f"ISIN: {len(by_isin.results)} hit(s) / {by_isin.stats.total} total, "
            f"WKN: {len(by_wkn.results)}, name: {len(by_name.results)}"
        )

    def _instrument(self) -> str:
        instrument = self.client.instrument.get(self.config.isin)
        if instrument.isin != self.config.isin:
            raise RuntimeError(f"asked for {self.config.isin}, got {instrument.isin}")
        if not instrument.trading_venues:
            raise RuntimeError("instrument has no trading venues")
        return (
            f"{instrument.short_name} ({instrument.type}), "
            f"{len(instrument.trading_venues)} venue(s), {instrument.quote_currency_code}, "
            f"tradable={instrument.tradable}"
        )

    def _detail(self) -> str:
        detail = self.client.instrument.detail(self.config.isin)
        if detail.isin != self.config.isin:
            raise RuntimeError(f"asked for {self.config.isin}, got {detail.isin}")
        if not detail.tk_identifier:
            raise RuntimeError("detail has an empty tkIdentifier, chart would fail")
        return (
            f"{detail.name}, last {detail.last:,.2f} {detail.currency}, "
            f"tkIdentifier={detail.tk_identifier!r}"
        )

    def _chart(self) -> str:
        bars = self.client.instrument.chart(self.config.isin)
        if not bars:
            raise RuntimeError("chart returned no bars")
        if bars[-1].close <= 0:
            raise RuntimeError(f"last chart bar has a non-positive close: {bars[-1].close!r}")
        return (
            f"{len(bars)} bars, {bars[0].date} .. {bars[-1].date}, "
            f"last close {bars[-1].close:,.2f}"
        )

    def _positions(self) -> str:
        positions = self.client.portfolio.get_positions()
        value = sum((position.quote * position.quantity for position in positions), Decimal("0"))
        return f"{len(positions)} position(s), current value {value:,.2f} EUR"

    def _watchlist_get(self) -> str:
        entries = self.client.watchlist.get()
        for entry in entries:
            if entry.instrument.isin != entry.watchlist_item.isin:
                raise RuntimeError(
                    f"watchlist entry mismatch: {entry.instrument.isin} != "
                    f"{entry.watchlist_item.isin}"
                )
        self.watchlist_isins = {entry.watchlist_item.isin for entry in entries}
        return f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}"

    def _run_watchlist_mutation(self) -> None:
        if not self.config.mutations:
            self.runner.skip("watchlist.add/remove", "--skip-mutations")
            return
        if self.watchlist_isins is None:
            self.runner.skip("watchlist.add/remove", "watchlist.get failed")
            return
        target = self._pick_mutation_isin(self.watchlist_isins)
        if target is None:
            self.runner.skip(
                "watchlist.add/remove",
                "all candidate ISINs are already on the watchlist; pass --watch-isin",
            )
            return
        self.runner.check(
            "watchlist.add/remove",
            lambda: self._watchlist_round_trip(target),
        )

    def _pick_mutation_isin(self, present: set[str]) -> str | None:
        if self.config.watch_isin is not None:
            return None if self.config.watch_isin in present else self.config.watch_isin
        return next((isin for isin in DEFAULT_WATCH_ISINS if isin not in present), None)

    def _watchlist_round_trip(self, target: str) -> str:
        before = self.watchlist_isins
        if before is None:
            raise RuntimeError("watchlist state unknown")
        added = False
        completed = False
        try:
            entry = self.client.watchlist.add_to_watchlist(target)
            added = True
            if entry.instrument.isin != target or entry.watchlist_item.isin != target:
                raise RuntimeError(
                    f"add returned {entry.instrument.isin}/{entry.watchlist_item.isin}, "
                    f"expected {target}"
                )
            after_add = self._watchlist_state()
            if target not in after_add:
                raise RuntimeError(f"{target} was added but does not appear in GET /api/wl")
            if len(after_add) != len(before) + 1:
                raise RuntimeError(
                    f"watchlist went from {len(before)} to {len(after_add)} entries, "
                    "expected exactly one more"
                )

            self.client.watchlist.remove_from_watchlist(target)
            added = False
            after_remove = self._watchlist_state()
            if target in after_remove:
                raise RuntimeError(f"{target} is still in the watchlist after remove")
            if after_remove != before:
                raise RuntimeError("watchlist did not return to its previous set of ISINs")
            completed = True
            return f"added and removed {target}; restored to {len(before)} entries"
        finally:
            if not completed and not added:
                # The add response can fail after the server already stored the
                # item (network drop, schema drift); check before giving up.
                try:
                    if target in self._watchlist_state():
                        added = True
                except Exception:
                    pass
            if added:
                try:
                    self.client.watchlist.remove_from_watchlist(target)
                except Exception as exc:
                    warning = (
                        f"cleanup failed for {target}: {_describe(exc)} - "
                        "remove it manually from the watchlist"
                    )
                    self.cleanup_warnings.append(warning)
                    print(f"  WARN  {warning}", file=sys.stderr)

    def _watchlist_state(self) -> set[str]:
        return {entry.watchlist_item.isin for entry in self.client.watchlist.get()}

    def _orders(self, status: str) -> str:
        page = self.client.orders.list(status)
        has_next = ", hasNext=true" if page.has_next else ""
        return f"{len(page.list)} order(s) with status={status}{has_next}"

    def _order_lists_ok(self) -> bool:
        expected = {f"orders.list {status}" for status in ORDER_STATUSES}
        results = [result for result in self.runner.results if result.name in expected]
        return len(results) == len(ORDER_STATUSES) and all(
            result.status == "PASS" for result in results
        )

    # -- live trade -----------------------------------------------------

    def _run_trade(self) -> None:
        if self.config.trade_isin is None:
            self.runner.skip(
                "trade.buy 1 @ market",
                "pass --trade-isin to enable the live round-trip",
            )
            self.runner.skip("trade.sell 1 @ market", "no live buy")
            return
        if not self._order_lists_ok():
            self.runner.skip(
                "trade.buy 1 @ market",
                "order-list checks failed; refusing to place a trade we cannot verify",
            )
            self.runner.skip("trade.sell 1 @ market", "no live buy")
            return
        self.runner.check("trade.buy 1 @ market", self._trade_buy)
        if not self.buy_filled:
            self.runner.skip("trade.sell 1 @ market", "buy not confirmed filled")
            return
        self.runner.check("trade.sell 1 @ market", self._trade_sell)

    def _trade_buy(self) -> str:
        plan = self._trade_preflight()
        self.trade_plan = plan
        self._confirm_trade(plan)
        device = self._ensure_2fa()
        try:
            order = self.client.orders.place(self._market_order("buy", plan), device=device)
        except Exception as exc:
            raise _TradeError(
                f"buy placement failed or its outcome is unknown: {_describe(exc)}. "
                "Check the order list before rerunning."
            ) from exc
        print(f"    buy order {order.order_id} placed (status={order.status})")
        try:
            filled = self._await_fill(order.order_id, "buy", device)
        except _TradeError:
            raise
        except KeyboardInterrupt:
            self._best_effort_cancel(order.order_id, "buy", device)
            raise
        except Exception as exc:
            raise _TradeError(
                f"buy order {order.order_id} was placed but its fill could not be confirmed: "
                f"{_describe(exc)}. Check the order list; no sell was placed."
            ) from exc
        self.buy_order = filled
        self.buy_filled = True
        return self._fill_detail(filled, plan, "buy")

    def _trade_sell(self) -> str:
        plan = self.trade_plan
        buy = self.buy_order
        if plan is None or buy is None or not self.buy_filled:
            raise _TradeError("buy is not confirmed filled; refusing to sell")
        device: EnrolledDevice | None = None
        order: Order | None = None
        try:
            device = self._ensure_2fa()
            order = self.client.orders.place(self._market_order("sell", plan), device=device)
            print(f"    sell order {order.order_id} placed (status={order.status})")
            filled = self._await_fill(order.order_id, "sell", device)
        except KeyboardInterrupt:
            if order is not None:
                self._best_effort_cancel(order.order_id, "sell", device)
            raise
        except Exception as exc:
            raise _TradeError(
                f"sell step failed after the buy filled: {_describe(exc)}. "
                "Check the depot - you may still hold the share."
            ) from exc
        self.sell_order = filled
        detail = self._fill_detail(filled, plan, "sell")
        if buy.exec_price is not None and filled.exec_price is not None:
            gross = (filled.exec_price - buy.exec_price) * TRADE_QUANTITY
            detail += f"; gross {gross:+,.2f} {plan.currency} before fees"
        return detail

    def _trade_preflight(self) -> TradePlan:
        isin = self.config.trade_isin
        if isin is None:
            raise _TradeError("no --trade-isin configured")
        instrument = self.client.instrument.get(isin)
        if instrument.isin != isin:
            raise _TradeError(f"asked for {isin}, broker returned instrument {instrument.isin}")
        if instrument.tradable != "OPEN":
            raise _TradeError(f"{isin} is not currently tradable (tradable={instrument.tradable!r})")

        detail = self.client.instrument.detail(isin)
        reference_price = Decimal(str(detail.last)) if detail.last > 0 else Decimal("0")
        if reference_price <= 0 and instrument.historic_quotes:
            reference_price = instrument.historic_quotes[0].ask
        if reference_price <= 0:
            raise _TradeError(f"no usable reference price for {isin}")

        venue = self._select_venue(instrument)
        currency = (detail.currency or instrument.quote_currency_code).upper()
        self._validate_quantity(instrument, reference_price, currency)
        self._validate_market_hours(instrument, venue)

        account = self.client.account.get()
        if currency == "EUR":
            required = reference_price + Decimal("1")  # observed minimum-quantity surcharge
            if account.buying_power < required:
                raise _TradeError(
                    f"buying power {account.buying_power:,.2f} EUR is below the estimated "
                    f"{required:,.2f} EUR (reference price + 1 EUR surcharge); no order placed"
                )
        else:
            print(
                f"    warning: instrument quotes in {currency}; skipping the EUR "
                "buying-power precheck, the broker will decide"
            )

        try:
            positions = self.client.portfolio.get_positions()
        except Exception as exc:
            print(f"    warning: could not read existing positions: {_describe(exc)}")
            positions = []
        existing = sum(
            (position.quantity for position in positions if position.isin == isin),
            Decimal("0"),
        )
        print(
            f"    preflight: {instrument.short_name} ({isin}), {instrument.type}, "
            f"reference {reference_price:,.2f} {currency}, "
            f"buying power {account.buying_power:,.2f} EUR"
        )
        print(
            f"    venue: {venue.trading_venue_name} ({venue.mic_code}), "
            f"{venue.trading_hours_start}-{venue.trading_hours_stop} Europe/Berlin"
        )
        if existing > 0:
            print(
                f"    existing position: {existing} - the sell reduces the depot by 1 "
                "(FIFO, not necessarily the share just bought)"
            )
        return TradePlan(
            isin=isin,
            name=instrument.short_name,
            instrument_type=instrument.type,
            venue_name=venue.trading_venue_name,
            mic_code=venue.mic_code,
            reference_price=reference_price,
            currency=currency,
            existing_quantity=existing,
        )

    def _select_venue(self, instrument: Instrument) -> TradingVenue:
        if any(
            exec_type.lower() == ExecType.MARKET.value
            for exec_type in instrument.disabled_exec_types
        ):
            raise _TradeError(f"market orders are disabled for {instrument.isin}")
        venues = instrument.trading_venues
        if not venues:
            raise _TradeError(f"{instrument.isin} has no trading venues")
        if self.config.mic is not None:
            wanted = self.config.mic.upper()
            venue = next((item for item in venues if item.mic_code.upper() == wanted), None)
            if venue is None:
                choices = ", ".join(
                    f"{item.mic_code} ({item.trading_venue_name})" for item in venues
                )
                raise _TradeError(
                    f"MIC {wanted} is not available for {instrument.isin}; choose one of: {choices}"
                )
            return venue
        default = next((item for item in venues if item.default_venue), None)
        return default or venues[0]

    def _validate_quantity(
        self,
        instrument: Instrument,
        reference_price: Decimal,
        currency: str,
    ) -> None:
        if TRADE_QUANTITY < instrument.min_qty:
            raise _TradeError(
                f"1 unit is below the minimum quantity {instrument.min_qty} for {instrument.isin}"
            )
        if instrument.qty_incr > 0:
            remainder = (TRADE_QUANTITY - instrument.min_qty) % instrument.qty_incr
            if remainder != 0:
                raise _TradeError(
                    f"1 unit is not a valid quantity for {instrument.isin} "
                    f"(minimum {instrument.min_qty}, increment {instrument.qty_incr})"
                )
        order_value = reference_price * TRADE_QUANTITY
        if order_value < instrument.minimum_order_value:
            raise _TradeError(
                f"1 unit (~{order_value:,.2f} {currency}) is below the minimum order value "
                f"{instrument.minimum_order_value:,.2f} {currency}"
            )
        if (
            instrument.max_amount is not None
            and instrument.max_amount > 0
            and order_value > instrument.max_amount
        ):
            raise _TradeError(
                f"1 unit (~{order_value:,.2f} {currency}) exceeds the maximum order amount "
                f"{instrument.max_amount:,.2f} {currency}"
            )

    def _validate_market_hours(self, instrument: Instrument, venue: TradingVenue) -> None:
        now = datetime.now(TRADE_TIMEZONE)
        if now.weekday() >= 5 and not (venue.weekend_trading and instrument.weekend_trading):
            raise _TradeError(
                f"{venue.trading_venue_name} ({venue.mic_code}) / {instrument.isin} "
                f"does not trade on weekends (now {now:%a %H:%M} Europe/Berlin); "
                "no order placed"
            )
        start = _minutes(venue.trading_hours_start)
        stop = _minutes(venue.trading_hours_stop)
        current = now.hour * 60 + now.minute
        if start == stop:
            is_open = True
        elif start < stop:
            is_open = start <= current < stop
        else:
            is_open = current >= start or current < stop
        if is_open:
            return
        choices = "; ".join(
            f"{item.trading_venue_name} ({item.mic_code}) "
            f"{item.trading_hours_start}-{item.trading_hours_stop}"
            for item in instrument.trading_venues
        )
        raise _TradeError(
            f"{venue.trading_venue_name} ({venue.mic_code}) is outside its trading hours "
            f"{venue.trading_hours_start}-{venue.trading_hours_stop} Europe/Berlin "
            f"(now {now:%a %H:%M}); a market order would be rejected. Venues: {choices}. "
            "Use --mic to select another venue."
        )

    def _confirm_trade(self, plan: TradePlan) -> None:
        print()
        print("  LIVE ORDERS - this uses real money")
        print(
            f"    BUY  1 x {plan.name} ({plan.isin}, {plan.instrument_type}) at market on "
            f"{plan.venue_name} ({plan.mic_code})"
        )
        print("    SELL 1 x at market only AFTER the buy is confirmed filled")
        print(
            f"    reference price {plan.reference_price:,.2f} {plan.currency}; "
            "market price may move; fees apply"
        )
        if plan.existing_quantity > 0:
            print(
                f"    existing position: {plan.existing_quantity}; the sell reduces it by 1 (FIFO)"
            )
        if self.config.assume_yes:
            print("    confirmed by --yes")
            return
        if not sys.stdin.isatty():
            raise _TradeError("live trade needs --yes when stdin is not a TTY")
        try:
            answer = input("  Type 'yes' to place the live buy and sell: ").strip().lower()
        except EOFError:
            raise _TradeError("no confirmation received; no orders placed") from None
        if answer not in {"yes", "y"}:
            raise _SkipTrade("live trade declined; no orders placed")

    def _ensure_2fa(self) -> EnrolledDevice | None:
        if self.client.sessiontan.is_authorized():
            print("    2FA: order-execution session already unlocked")
            return None
        devices = self.client.sessiontan.enrolled_devices()
        selected = self._select_device(devices)
        print(
            f"    2FA: locked; sending push to {selected.customer_device_name} "
            f"({selected.serial}). Approve it in the ZERO app."
        )
        self.client.sessiontan.unlock(selected, timeout=self.config.auth_timeout)
        if not self.client.sessiontan.is_authorized():
            raise _TradeError("2FA unlock completed but the session still reports locked")
        print("    2FA: unlocked")
        return selected

    def _select_device(self, devices: list[EnrolledDevice]) -> EnrolledDevice:
        if not devices:
            raise _TradeError("2FA is locked but no device is enrolled")
        if self.config.device is not None:
            selected = next(
                (device for device in devices if device.serial == self.config.device), None
            )
            if selected is None:
                choices = ", ".join(
                    f"{device.serial} ({device.customer_device_name})" for device in devices
                )
                raise _TradeError(
                    f"--device {self.config.device!r} is not enrolled; available: {choices}"
                )
            return selected
        if len(devices) == 1:
            return devices[0]
        choices = ", ".join(
            f"{device.serial} ({device.customer_device_name})" for device in devices
        )
        if not sys.stdin.isatty():
            raise _TradeError(
                f"multiple 2FA devices and no TTY to choose one; pass --device. Available: {choices}"
            )
        print("  Choose the 2FA device for the approval push:")
        for index, device in enumerate(devices, start=1):
            print(
                f"    {index}. {device.customer_device_name} - {device.serial} "
                f"({device.device_manufacturer} {device.device_model})"
            )
        while True:
            try:
                raw = input(f"  Device [1-{len(devices)}] or serial: ").strip()
            except EOFError:
                raise _TradeError("no 2FA device selected") from None
            if raw.isdigit():
                index = int(raw)
                if 1 <= index <= len(devices):
                    return devices[index - 1]
            selected = next((device for device in devices if device.serial == raw), None)
            if selected is not None:
                return selected
            print(f"  Not a valid choice: {raw!r}")

    @staticmethod
    def _market_order(direction: str, plan: TradePlan) -> OrderRequest:
        return OrderRequest(
            isin=plan.isin,
            direction=direction,
            exec_type=ExecType.MARKET,
            quantity=TRADE_QUANTITY,
            mic_code=plan.mic_code,
        )

    def _await_fill(
        self,
        order_id: str,
        action: str,
        device: EnrolledDevice | None,
    ) -> Order:
        deadline = monotonic() + self.config.fill_timeout
        last: Order | None = None
        last_status: str | None = None
        while True:
            order = self._find_order(order_id)
            if order is not None:
                last = order
                if order.executed_quantity >= TRADE_QUANTITY and (
                    order.status in FILLED_STATUSES or order.open_quantity <= 0
                ):
                    print(
                        f"    {action}: confirmed in the order lists - status={order.status}, "
                        f"executed={order.executed_quantity}/{TRADE_QUANTITY}, "
                        f"open={order.open_quantity}"
                    )
                    return order
                if order.status != last_status:
                    print(
                        f"    {action}: status={order.status}, "
                        f"executed={order.executed_quantity}/{TRADE_QUANTITY}, "
                        f"open={order.open_quantity}"
                    )
                    last_status = order.status
                if order.status in FILLED_STATUSES:
                    raise _TradeError(
                        f"{action} order {order_id} is {order.status} but only "
                        f"{order.executed_quantity}/{TRADE_QUANTITY} executed"
                    )
                if order.status in FINAL_UNFILLED_STATUSES:
                    if order.executed_quantity > 0:
                        raise _TradeError(
                            f"{action} order {order_id} ended as {order.status} with a "
                            f"partial fill ({order.executed_quantity}/{TRADE_QUANTITY}); stopping"
                        )
                    raise _TradeError(
                        f"{action} order {order_id} ended as {order.status} without filling"
                    )
            if monotonic() >= deadline:
                return self._handle_fill_timeout(order_id, action, last, device)
            sleep(self.config.poll_interval)

    def _handle_fill_timeout(
        self,
        order_id: str,
        action: str,
        last: Order | None,
        device: EnrolledDevice | None,
    ) -> Order:
        timeout = self.config.fill_timeout
        follow_up = (
            "no sell was placed"
            if action == "buy"
            else "check the depot - you may still hold the share"
        )
        if last is None:
            raise _TradeError(
                f"{action} order {order_id} was not visible in Open/Canceled/Filled within "
                f"{timeout:g}s; check the broker before doing anything else"
            )
        if last.executed_quantity >= TRADE_QUANTITY:
            return last
        if last.executed_quantity > 0:
            after = self._cancel_order(order_id, action, device)
            if after is not None and after.executed_quantity >= TRADE_QUANTITY:
                return after
            raise _TradeError(
                f"{action} order {order_id} only partially filled "
                f"({last.executed_quantity}/{TRADE_QUANTITY}) within {timeout:g}s; "
                f"cancel requested; {follow_up}"
            )
        if last.open_quantity > 0:
            after = self._cancel_order(order_id, action, device)
            if after is not None and after.executed_quantity >= TRADE_QUANTITY:
                return after
            final_status = after.status if after is not None else "unknown"
            raise _TradeError(
                f"{action} order {order_id} did not fill within {timeout:g}s; "
                f"cancel requested (status={final_status}); {follow_up}"
            )
        raise _TradeError(
            f"{action} order {order_id} did not fill within {timeout:g}s "
            f"(status={last.status}, open={last.open_quantity}); {follow_up}"
        )

    def _cancel_order(
        self,
        order_id: str,
        action: str,
        device: EnrolledDevice | None,
    ) -> Order | None:
        try:
            transition = self.client.orders.cancel(order_id, device=device)
        except Exception as exc:
            # The order may have filled just before the cancel landed; check
            # before reporting a cancellation failure.
            try:
                found = self._find_order(order_id)
            except Exception:
                found = None
            if found is not None and found.executed_quantity >= TRADE_QUANTITY:
                return found
            raise _TradeError(
                f"{action} order {order_id} is still open and cancellation failed: "
                f"{_describe(exc)}; cancel it manually"
            ) from exc
        print(f"    {action}: cancel requested (status={transition.status})")
        deadline = monotonic() + max(10.0, self.config.poll_interval * 5)
        last: Order | None = transition
        while monotonic() < deadline:
            sleep(self.config.poll_interval)
            found = self._find_order(order_id)
            if found is None:
                continue
            last = found
            if found.executed_quantity >= TRADE_QUANTITY:
                return found
            if found.status in FINAL_UNFILLED_STATUSES or found.open_quantity <= 0:
                break
        if last is not None:
            print(
                f"    {action}: after cancel - status={last.status}, "
                f"executed={last.executed_quantity}/{TRADE_QUANTITY}, "
                f"open={last.open_quantity}"
            )
        return last

    def _best_effort_cancel(
        self,
        order_id: str,
        action: str,
        device: EnrolledDevice | None,
    ) -> None:
        """On Ctrl-C, try to cancel an order that may still be open."""
        try:
            found = self._find_order(order_id)
        except Exception as exc:
            print(
                f"  WARN  {action} order {order_id}: could not inspect after interrupt: "
                f"{_describe(exc)}",
                file=sys.stderr,
            )
            return
        if found is not None and found.executed_quantity >= TRADE_QUANTITY:
            print(f"    {action}: already filled; not canceling")
            return
        if found is not None and found.status in FINAL_UNFILLED_STATUSES:
            print(f"    {action}: already final ({found.status}); not canceling")
            return
        try:
            self._cancel_order(order_id, action, device)
        except Exception as exc:
            print(
                f"  WARN  {action} order {order_id}: cancel after interrupt failed: "
                f"{_describe(exc)}; check it manually",
                file=sys.stderr,
            )

    def _find_order(self, order_id: str) -> Order | None:
        # Final states first: a transitioned order can briefly appear in Open
        # while its final row is already in Filled or Canceled.
        for status in ("Filled", "Canceled", "Open"):
            page = self.client.orders.list(status)
            for order in page.list:
                if order.order_id == order_id:
                    return order
        return None

    @staticmethod
    def _fill_detail(order: Order, plan: TradePlan, side: str) -> str:
        price = "n/a" if order.exec_price is None else f"{order.exec_price:,.2f} {plan.currency}"
        return (
            f"{side} order {order.order_id} filled "
            f"{order.executed_quantity}/{TRADE_QUANTITY} at {price} on "
            f"{plan.venue_name} (status={order.status})"
        )

    def _is_authorized(self) -> str:
        authorized = self.client.sessiontan.is_authorized()
        return "order-execution session unlocked" if authorized else "session locked (no 2FA used)"

    def _devices(self) -> str:
        devices = self.client.sessiontan.enrolled_devices()
        if not devices:
            return "no enrolled 2FA devices"
        return f"{len(devices)} enrolled 2FA device(s)"

    # -- reporting ------------------------------------------------------

    def print_summary(self, elapsed: float) -> None:
        runner = self.runner
        print()
        print(
            f"{runner.passed} passed, {runner.failed} failed, {runner.skipped} skipped "
            f"in {elapsed:.2f}s"
        )
        if self.cleanup_warnings:
            print(
                f"{len(self.cleanup_warnings)} watchlist cleanup warning(s): "
                + "; ".join(self.cleanup_warnings),
                file=sys.stderr,
            )

    @property
    def exit_code(self) -> int:
        return 0 if self.runner.failed == 0 and not self.cleanup_warnings else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke test zero-api. Read-only checks always run; --trade-isin enables "
            "a real-money market buy/sell round-trip."
        ),
    )
    parser.add_argument(
        "--email",
        default=None,
        help="ZERO email (default: ZERO_EMAIL environment variable; prompts if absent)",
    )
    parser.add_argument(
        "--isin",
        default=None,
        help=(
            "instrument used for the read-only checks "
            f"(default: --trade-isin, else {DEFAULT_ISIN})"
        ),
    )
    parser.add_argument(
        "--trade-isin",
        default=None,
        help="enable the live round-trip: buy exactly 1, wait for the fill, then sell exactly 1",
    )
    parser.add_argument(
        "--watch-isin",
        default=None,
        help="instrument for the watchlist add/remove round-trip (default: first free candidate)",
    )
    parser.add_argument(
        "--mic",
        default=None,
        help="venue MIC for the live trade (default: the instrument's default venue)",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="2FA device serial to use when the order-execution session is locked",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the live-trade confirmation prompt (still requires --trade-isin)",
    )
    parser.add_argument(
        "--fill-timeout",
        type=float,
        default=60.0,
        help="seconds to wait for each order to fill (default: 60)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="seconds between order-list polls (default: 2)",
    )
    parser.add_argument(
        "--auth-timeout",
        type=float,
        default=120.0,
        help="seconds to wait for the 2FA push approval (default: 120)",
    )
    parser.add_argument(
        "--skip-mutations",
        action="store_true",
        help="do not touch the watchlist (add/remove round-trip is skipped)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print full tracebacks for failed checks",
    )
    return parser


def _config_from_args(args: argparse.Namespace) -> Config:
    trade_isin = str(args.trade_isin).strip().upper() if args.trade_isin is not None else None
    if args.trade_isin is not None and not trade_isin:
        _config_error("--trade-isin must not be empty")
    fill_timeout = float(args.fill_timeout)
    poll_interval = float(args.poll_interval)
    auth_timeout = float(args.auth_timeout)
    if fill_timeout <= 0:
        _config_error("--fill-timeout must be greater than 0")
    if poll_interval <= 0:
        _config_error("--poll-interval must be greater than 0")
    if auth_timeout <= 0:
        _config_error("--auth-timeout must be greater than 0")
    return Config(
        email=str(args.email) if args.email is not None else None,
        isin=(
            str(args.isin).strip().upper()
            if args.isin is not None
            else (trade_isin or DEFAULT_ISIN)
        ),
        trade_isin=trade_isin,
        watch_isin=(
            str(args.watch_isin).strip().upper() if args.watch_isin is not None else None
        ),
        mic=str(args.mic) if args.mic is not None else None,
        device=str(args.device) if args.device is not None else None,
        mutations=not bool(args.skip_mutations),
        verbose=bool(args.verbose),
        assume_yes=bool(args.yes),
        fill_timeout=fill_timeout,
        poll_interval=poll_interval,
        auth_timeout=auth_timeout,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = _config_from_args(args)
    email, password = _resolve_credentials(config.email)

    print("zero-api live smoke test")
    print(f"  endpoint: {BASE_URL}")
    print(f"  target ISIN (read-only checks): {config.isin}")
    print(
        "  watchlist mutations: "
        + ("on (add/remove round-trip, restored)" if config.mutations else "off")
    )
    print(
        "  live trade: "
        + (
            f"ON - buy/sell 1 x {config.trade_isin} at market (real money)"
            if config.trade_isin is not None
            else "off (pass --trade-isin to enable; real money)"
        )
    )
    print()

    client = ZeroClient(email=email, password=password)
    smoke = SmokeTest(client, config)
    started = perf_counter()
    try:
        smoke.run()
    except _AbortSmoke:
        print("\nauthentication failed - aborting the remaining checks", file=sys.stderr)
    except KeyboardInterrupt:
        print(
            "\ninterrupted - if a live trade was in progress, check open orders and the depot",
            file=sys.stderr,
        )
        smoke.print_summary(perf_counter() - started)
        return 130

    smoke.print_summary(perf_counter() - started)
    return smoke.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
