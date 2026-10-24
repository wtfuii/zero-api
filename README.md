# zero-api

Unofficial Python client for the German broker **Finanzen.net ZERO**.

There is no official API. This library mimics the broker's private HTTP endpoints, which were reverse-engineered from the app's own traffic. It covers login, account data, instrument data, the depot, the watchlist, order placement/cancellation, and the app-push 2FA unlock — all over plain HTTP.

![Python](https://img.shields.io/badge/python-3.13%2B-blue)
![Typing](https://img.shields.io/badge/mypy-strict-success)
![Tests](https://img.shields.io/badge/tests-passing-success)

> **Unofficial and unsupported.** Endpoints are private and can change without notice. Use at your own risk, and never against an account you are not allowed to automate.

---

## Features

- **Email + password login over HTTP** — Cognito SRP through the broker's own `/apic/authc` proxy, no browser required.
- **Automatic session handling** — logs in lazily on the first call, refreshes the IdToken from the refresh-token cookie, and re-authenticates once on `401`/`403`.
- **Typed models everywhere** — every response is a Pydantic model with `extra="forbid"`, so broker schema drift fails loudly instead of silently corrupting data.
- **Full order flow** — `verify` precheck, ex-ante cost document (`/apic/exante`), placement, listing and cancellation.
- **2FA order execution** — probes, lists enrolled devices, sends the app push, polls for approval, and unlocks the session (or auto-unlocks inside `place()`/`cancel()`).
- **Tested offline** — the test suite runs against recorded, sanitized broker payloads. No live broker calls, ever.

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.13+** | The project uses `StrEnum`, PEP 604 unions and modern typing. |
| **[uv](https://docs.astral.sh/uv/)** | Used for dependency management, running mypy and pytest. |
| A **Finanzen.net ZERO account** | Needed for real calls. Unit tests need no account. |
| The **ZERO mobile app** | Only for order execution: 2FA pushes are approved there. |

`httpx` and `pydantic` are installed automatically.

## Installation

The package is not published on PyPI. Install it from source:

```bash
git clone <repository-url> zero-api
cd zero-api
uv sync
```

Then use it from your own project, either by adding the local path as a dependency:

```bash
uv add --editable ../zero-api
```

or by running scripts inside the checked-out repository with `uv run`.

## Quick start

```python
from zero_api import ZeroClient

client = ZeroClient(email="you@example.com", password="secret")

account = client.account.get()
print(account.buying_power, account.account_iban)

for position in client.portfolio.get_positions():
    print(position.isin, position.asset_name, position.quantity, position.quote)
```

The constructor does not touch the network. The first API call performs the login; later calls reuse the in-memory cookie session.

## Authentication and sessions

`ZeroClient(email, password)` keeps credentials in memory and establishes the session on demand:

1. Full SRP login (`http_login`) if no session exists.
2. Refresh from the `__r` cookie (`http_refresh`) when the IdToken is close to expiry.
3. One automatic re-login and retry when the broker answers `401`/`403`.

Nothing is persisted to disk. A new process logs in again.

For custom setups, the client accepts two escape hatches:

```python
import httpx
from zero_api import ZeroClient
from zero_api.auth.api import http_login
from zero_api.auth.models import AuthSession

# Reuse your own httpx client (proxy, custom transport, timeouts).
client = ZeroClient(email="you@example.com", password="secret", http=httpx.Client())

# Supply a pre-existing or externally obtained session instead.
client = ZeroClient(
    email="you@example.com",
    password="secret",
    login=lambda: AuthSession.from_cookies(cookies),
)
```

`AuthSession` exposes `cookies`, `customer_id` and `id_token_expired()`. Raw helpers are available in `zero_api.auth.api`:

| Function | Description |
|---|---|
| `http_login(http, email, password) -> AuthSession` | Full Cognito SRP login on the given `httpx.Client`, storing the `__r`/`__i` cookies. |
| `http_refresh(http, session) -> AuthSession` | Cheap refresh-token exchange, no SRP round. Cognito does not rotate the refresh token. |
| `AuthSession.from_cookies(cookies)` | Build a session from a cookie jar; `customer_id` is read from the IdToken `prm` claim. |

## API reference

Everything hangs off one client object:

```python
from zero_api import ZeroClient

client = ZeroClient(email="...", password="...")
```

Constructor:

```python
ZeroClient(
    email: str,
    password: str,
    *,
    login: Callable[[], AuthSession] | None = None,
    http: httpx.Client | None = None,
    default_device: str | EnrolledDevice | None = None,
) -> None
```

| Parameter | Description |
|---|---|
| `email`, `password` | Broker credentials, used for the HTTP SRP login and automatic re-login. |
| `login` | Optional callable returning an `AuthSession`; use it to inject a session you obtained elsewhere. |
| `http` | Optional `httpx.Client` (proxy, custom transport, timeouts). Defaults to a client for `https://mein.finanzen-zero.net`. |
| `default_device` | Default 2FA push target (serial or `EnrolledDevice`) for `place()`/`cancel()`/`unlock()`. |

The client exposes six slices: `account`, `instrument`, `portfolio`, `watchlist`, `orders`, `sessiontan`.

### `client.account`

| Method | Returns | Description |
|---|---|---|
| `get()` | `MoneyTransactionAccount` | Cash account (Verrechnungskonto): buying power, transfer power, IBAN, saldo, closing period. |

Key fields: `buying_power`, `transfer_power`, `intraday_amount`, `account_iban`, `account_nr`, `customer_id`, `overdrawn`, `next_closing_date`.

```python
account = client.account.get()
print(f"Buying power: {account.buying_power} EUR")
```

### `client.instrument`

| Method | Returns | Description |
|---|---|---|
| `get(isin)` | `Instrument` | Master record: trading rules, venues, per-venue fees, quotes, sentiment. |
| `detail(isin)` | `InstrumentDetail` | Market data and fundamentals: price, 52-week range, volume, profile, dividends. |
| `chart(isin, period=ChartPeriod.ONE_YEAR)` | `list[ChartBar]` | Chart series for one period. Fetches `detail()` first to resolve the chart identifier. |

`Instrument` exposes `trading_venues` (with `mic_code`, trading hours, fees) and `trading_fees`. `ChartBar` always has `date` and `close`; `open_`, `high`, `low` and `volume` may be `None` depending on the period.

```python
from zero_api.instrument.models import ChartPeriod

instrument = client.instrument.get("DE0007030009")
for venue in instrument.trading_venues:
    print(venue.mic_code, venue.trading_venue_name, venue.default_venue)

bars = client.instrument.chart("DE0007030009", ChartPeriod.ONE_MONTH)
for bar in bars:
    print(bar.date, bar.close)
```

### `client.portfolio`

| Method | Returns | Description |
|---|---|---|
| `get_positions()` | `list[Position]` | Current depot positions with quantity, average entry quote and latest quote. |

`Position` fields include `isin`, `asset_name`, `quantity`, `available_quantity`, `avg_entry_quote`, `quote`, `quote_time`.

```python
from decimal import Decimal

positions = client.portfolio.get_positions()

def value(position):
    return position.quote * position.quantity

total = sum((value(p) for p in positions), Decimal("0"))
print(f"Depot value: {total} EUR")
```

### `client.watchlist`

| Method | Returns | Description |
|---|---|---|
| `get()` | `list[WatchlistEntry]` | The watchlist with instrument data and watchlist metadata (order, added timestamp, quotes). |
| `add_to_watchlist(isin)` | `WatchlistEntry` | Add an instrument by ISIN; the server prepends it with `wlOrder` 0. |
| `remove_from_watchlist(isin)` | `None` | Remove an instrument by ISIN (not by item id). Raises `ValueError` if the broker does not confirm with `true`. |

```python
client.watchlist.add_to_watchlist("DE0007030009")

for entry in client.watchlist.get():
    print(entry.instrument.short_name, entry.watchlist_item.bid, entry.watchlist_item.ask)

client.watchlist.remove_from_watchlist("DE0007030009")
```

### `client.orders`

| Method | Returns | Description |
|---|---|---|
| `list(status="Open", *, page=0, size=1000)` | `OrdersPage` | Orders by status. `OrdersPage` has `list: list[Order]` and `has_next`. |
| `place(order, *, device=None)` | `Order` | Verify, generate the ex-ante cost document, place the order. Returns it with status `"Created"`. |
| `cancel(order_id, *, device=None)` | `Order` | Cancel by the string `order_id`. Returns the order in a transition state (e.g. `"Open_CreatedCancel"`). |

`OrderRequest` describes the order:

```python
from decimal import Decimal
from zero_api.ordering.models import ExecType, OrderRequest

order = OrderRequest(
    isin="DE0007030009",
    direction="buy",            # "buy" / "sell"
    exec_type=ExecType.LIMIT,   # MARKET, LIMIT, TSL, OCO, STOP_LIMIT
    quantity=Decimal("1"),
    mic_code="XETR",            # target venue, from instrument.trading_venues
    limit_price=Decimal("500.00"),
)
placed = client.orders.place(order)  # 2FA unlock happens automatically; approve the push in the app
print(placed.order_id, placed.status)
```

`place()` and `cancel()` unlock the 2FA order-execution session in the background — **you do not need to touch `client.sessiontan` yourself before placing or deleting an order.** Pass `device` to choose the push target, otherwise the client's `default_device` is used, otherwise the single enrolled device. You still approve the push in the ZERO app on your phone; the library waits for that approval.

Omit `valid_to_date` for "now" (market orders) or set it for good-till-date limit orders:

```python
from datetime import UTC, datetime

order = OrderRequest(
    isin="DE0007030009",
    direction="sell",
    exec_type=ExecType.LIMIT,
    quantity=Decimal("1"),
    mic_code="XETR",
    limit_price=Decimal("600"),
    valid_to_date=datetime(2026, 12, 31, tzinfo=UTC),
)
```

Cancel:

```python
for order in client.orders.list().list:
    print(order.order_id, order.isin, order.status, order.quantity)
# client.orders.cancel(order.order_id)
```

### `client.sessiontan`

Handles the app-push 2FA that unlocks order execution for the web session. Endpoints are server-side session state; no extra tokens are involved.

**This slice is optional.** `orders.place()` and `orders.cancel()` call it automatically, so you only need these methods to pre-unlock the session, inspect its state, or choose a push device. For a single-device account, placing an order is enough.

| Method | Returns | Description |
|---|---|---|
| `is_authorized()` | `bool` | Is this session currently unlocked for order execution? |
| `enrolled_devices()` | `list[EnrolledDevice]` | Devices that can receive the push. |
| `request_auth(device_serial)` | `str` | Send the push; returns a pending auth handle. |
| `auth_state(auth_handle)` | `AuthState` | Current state (`PENDING` / `ACCEPTED`) of a push. |
| `abort(auth_handle)` | `None` | Cancel a pending push. |
| `unlock(device=None, *, timeout=60.0, poll_interval=2.0)` | `None` | Request a push and block until approved, then unlock. |

```python
devices = client.sessiontan.enrolled_devices()
print([(d.serial, d.customer_device_name) for d in devices])

# Unlock for a single push; approve it on the phone.
client.sessiontan.unlock(devices[0].serial)
print(client.sessiontan.is_authorized())  # True
```

For accounts with several enrolled devices, set the default once:

```python
client.sessiontan.default_device = "SERIAL-OF-MY-PHONE"
```

`unlock()` accepts a serial string or an `EnrolledDevice` and raises `TwoFactorError` on timeout, a declined push, an unknown state, or an ambiguous device selection.

## Errors

All exceptions derive from `ZeroApiError` in `zero_api.core.exceptions`:

| Exception | When |
|---|---|
| `ZeroApiError` | Base class — catch this to handle everything. |
| `AuthenticationError` | Login failed, refresh failed, or no login method is configured. |
| `TwoFactorError` | The 2FA push was not completed (timeout, unknown state, no usable device). |
| `BrokerApiError` | Non-2xx response with the broker's `apiError` body. Carries `status`, `message`, `error_code`, `errors`. |

Other non-2xx shapes (not the broker's `apiError` format) surface as `httpx.HTTPStatusError`, and unexpected success bodies as `ValueError` — both mean the broker drifted from the recorded behaviour.

```python
from zero_api.core.exceptions import BrokerApiError, TwoFactorError

try:
    client.orders.place(order)
except TwoFactorError as exc:
    print("2FA not completed:", exc)
except BrokerApiError as exc:
    print(exc.status, exc.error_code, exc.message, exc.errors)
```

## Notes and limitations

- **Private, undocumented endpoints.** Shapes were captured from the live app and are treated as reference data, not a stable contract. Models use `extra="forbid"` on purpose: a broker change surfaces as a validation error.
- **In-memory sessions only.** Cookies are not written to disk.
- **Unverified corners are marked in the code.** Examples: non-share instrument pages, unknown order statuses, declined 2FA states, and pagination of positions (`get_positions()` is not pageable).
- **The site is a moving target.** Chart rows vary by period; only `date` and `close` are guaranteed.
- **No rate-limit handling or retries** beyond the single re-authentication attempt.

## Development

```bash
uv sync
uv run mypy .      # strict, must pass first
uv run pytest      # full suite, all offline
```

Run a single test:

```bash
uv run pytest tests/ordering/test_ordering.py -k place
```

Conventions worth knowing:

- Features are **vertical slices** under `src/zero_api/<slice>/`, each owning its transport calls, models and public methods. Shared HTTP/auth lives in `core/`.
- Tests mirror the slices under `tests/<slice>/` and replay **recorded, sanitized broker payloads** from `tests/<slice>/fixtures/`; nothing hits the broker.
- `uv run mypy .` runs in strict mode over `src/` and `tests/`.

## Project layout

```
src/zero_api/
├── client.py          # ZeroClient — the only public entry point
├── core/              # shared HTTP transport + exceptions
├── auth/              # Cognito SRP login and refresh
├── account/           # cash account
├── instrument/        # master data, details, charts
├── portfolio/         # depot positions
├── watchlist/         # watchlist CRUD
├── ordering/          # orders: list, place, cancel
└── sessiontan/        # app-push 2FA unlock
tests/                 # offline tests + sanitized fixtures
```

## License

No license has been chosen for this project yet. Add a `LICENSE` file before distributing or accepting contributions.

## Disclaimer

This project is not affiliated with, endorsed by, or supported by Finanzen.net or Finanzen.net ZERO. It exists for personal, educational use. Automating a broker account may violate their terms of service; you are responsible for how you use it.
