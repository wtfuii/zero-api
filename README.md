<h1 align="center">zero-api</h1>

<p align="center">
  <strong>Unofficial Python client for the German broker Finanzen.net ZERO</strong> 🇩🇪<br>
  Typed, synchronous, and pure HTTP — no browser, no Selenium, no Playwright.
</p>

<p align="center">
  <a href="https://github.com/wtfuii/zero-api/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/wtfuii/zero-api/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/finanzen-zero-api/"><img alt="PyPI" src="https://img.shields.io/pypi/v/finanzen-zero-api?logo=pypi&logoColor=white"></a>
  <a href="https://www.python.org/downloads/"><img alt="Python 3.13+" src="https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white"></a>
  <a href="https://mypy.readthedocs.io/en/stable/"><img alt="mypy: strict" src="https://img.shields.io/badge/mypy-strict-2A6DB2"></a>
  <a href="#development"><img alt="Tests: offline only" src="https://img.shields.io/badge/tests-offline%20only-success"></a>
  <a href="https://github.com/wtfuii/zero-api/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-yellow.svg"></a>
  <a href="#project-status"><img alt="Status: alpha" src="https://img.shields.io/badge/status-alpha-orange.svg"></a>
</p>

> **Unofficial and unsupported.** Finanzen.net ZERO has no public API. This library mimics the
> broker's private web/app endpoints, which can change at any time. Use it at your own risk,
> only on an account you are allowed to automate, and never with money you cannot afford to lose.

`zero-api` wraps the core workflows of the ZERO web app in one typed Python object: account data,
instrument search and charts, depot (securities account) positions, the watchlist, order
placement/cancellation, and the app-push two-factor authentication (2FA) that unlocks order
execution.

```python
from zero_api import ZeroClient

client = ZeroClient(email="you@example.com", password="secret")
print(client.account.get().buying_power)
```

---

## Contents

- [Features](#features)
- [What's implemented](#whats-implemented)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Authentication and sessions](#authentication-and-sessions)
- [API guide](#api-guide)
  - [Cash account](#cash-account)
  - [Instruments](#instruments)
  - [Portfolio](#portfolio)
  - [Watchlist](#watchlist)
  - [Orders](#orders)
  - [2FA order unlock](#2fa-order-unlock)
- [Error handling](#error-handling)
- [Project status](#project-status)
- [Limitations and gotchas](#limitations-and-gotchas)
- [Development](#development)
- [Live smoke test](#live-smoke-test)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Login without a browser.** Cognito SRP runs over plain HTTP through the broker's own
  `/apic/authc` proxy. No Chromium, no AWS WAF cookie, no API key.
- **Automatic sessions.** The first API call logs in; the 1-hour IdToken is refreshed from the
  refresh-token cookie, and a rejected session is re-established once and retried.
- **Typed end to end.** Every response is a Pydantic model, account/position/order amounts are
  `Decimal`, and models use `extra="forbid"` so broker schema drift fails loudly instead of
  silently returning wrong data.
- **The broker's submit flow.** 2FA probe/unlock → `/verify` precheck → ex-ante cost document →
  placement, plus order history and cancellation for market, limit, stop-limit, trailing-stop and
  OCO orders.
- **App-push 2FA built in.** `place()` and `cancel()` unlock the order-execution session
  automatically and wait for the approval on your paired device.
- **Offline, reproducible tests.** The suite replays sanitized, recorded broker payloads through
  `httpx.MockTransport`. It never touches the network or a real account.

## What's implemented

| Status | Slice | What you get |
|---|---|---|
| ✅ | `client.account` | Cash account (Verrechnungskonto): buying/transfer power, IBAN, saldo, closing period. |
| ✅ | `client.instrument` | Typeahead search, master data (one model, typed `type`), market/fundamental detail, chart series. |
| ✅ | `client.portfolio` | Current depot positions with quantity, average entry quote and latest quote. |
| ✅ | `client.watchlist` | List, add and remove watchlist entries by ISIN. |
| ✅ | `client.orders` | List orders by status, place market/limit/stop-limit/trailing-stop/OCO orders, cancel them. |
| ✅ | `client.sessiontan` | Inspect, request and complete the app-push unlock for order execution. |

## Requirements

| Requirement | Why |
|---|---|
| **Python 3.13+** | The package targets 3.13 and is strictly typed throughout. |
| **A Finanzen.net ZERO account** | Email + password login is the only supported login method. There is no API key. |
| **The ZERO mobile app** | Required only for order execution: the 2FA push is approved on a paired device. Read-only calls work without it. |
| **Network access to `mein.finanzen-zero.net`** | That is where every endpoint lives. There is no sandbox or test environment. |
| **`httpx` and `pydantic`** | Installed automatically as the only runtime dependencies. |

## Installation

```bash
pip install finanzen-zero-api
```

or, with [uv](https://docs.astral.sh/uv/):

```bash
uv add finanzen-zero-api
```

Python **3.13+** is required. The PyPI distribution is named `finanzen-zero-api` (the name
`zero-api` was not available on PyPI); you import it as `zero_api`. `httpx` and `pydantic` are the
only runtime dependencies and are installed with the package. The wheel ships a
[`py.typed`](https://github.com/wtfuii/zero-api/blob/main/src/zero_api/py.typed) marker, so editors
and mypy see the types.

> **Alpha (0.1.0).** The API may still change between minor versions. Pin the version you depend on
> (`finanzen-zero-api==0.1.0`) until it stabilizes.

### From source

To work on the library itself, or to track unreleased changes:

```bash
git clone https://github.com/wtfuii/zero-api.git
cd zero-api
uv sync
```

Add that checkout to another project as an editable dependency:

```bash
uv add --editable /path/to/zero-api
# or, with plain pip
python -m pip install -e /path/to/zero-api
```

Inside the checkout, `uv run python your_script.py` uses the project environment.

## Quick start

```python
from zero_api import ZeroClient

client = ZeroClient(email="you@example.com", password="secret")

account = client.account.get()
print(f"Buying power: {account.buying_power:,.2f} EUR")

for position in client.portfolio.get_positions():
    value = position.quote * position.quantity
    print(f"{position.asset_name}: {position.quantity} x {position.quote} = {value}")
```

The constructor does **not** touch the network. The first API call performs the login; later calls
reuse the in-memory cookie session.

## Authentication and sessions

`ZeroClient(email, password)` keeps the credentials in memory and establishes the session lazily.
It has no credential-specific environment variables or config files; supply credentials (or a
`login` callback) explicitly and keep them out of source control.

The session lifecycle:

1. **First call:** full Cognito SRP login over HTTP (`USER_SRP_AUTH` + `PASSWORD_VERIFIER`).
2. **IdToken near expiry:** a cheap refresh-token exchange (falling back to a full login if the
   refresh token is rejected); Cognito does not rotate the refresh token.
3. **`401`/`403`:** one refresh-or-login attempt, then the request is retried exactly once.

Sessions are **in memory only**. Cookies are never written to disk, so a new process logs in
again. Keep the `ZeroClient` alive if you want to avoid repeated logins in a long-running job.

The login flow is email + password only. The app push is used solely to unlock **order
execution**, not to sign in. Because SRP signs a UTC timestamp, keep the host clock synchronized
(NTP); large clock skew can make the login fail with `AuthenticationError`.

### Advanced: custom HTTP client or pre-existing session

`ZeroClient` accepts two escape hatches:

```python
import httpx

from zero_api import ZeroClient
from zero_api.auth.models import AuthSession

# Your own transport: proxy, timeouts, connection limits.
http = httpx.Client(
    base_url="https://mein.finanzen-zero.net",  # required: request paths are relative
    timeout=httpx.Timeout(30.0),
)
client = ZeroClient(email="you@example.com", password="secret", http=http)

# Supply sessions yourself (e.g. from a secret store) instead of logging in.
client = ZeroClient(
    email="unused@example.com",   # still required by the signature
    password="unused",
    login=lambda: AuthSession.from_cookies(
        {"__i": "<IdToken>", "__r": "<RefreshToken>", "XSRF-TOKEN": "<uuid>"}
    ),
)
```

When `login` is set it takes precedence over the stored credentials for every (re)login, after
the refresh-token path is tried. `AuthSession.from_cookies()` requires the `__i` cookie and reads
the `customerId` from its `prm` claim; pass the rest of the session's cookies (`__r`,
`XSRF-TOKEN`) as well so refresh and state-changing calls keep working.

> **Lifecycle:** there is no `ZeroClient.close()` or `logout()`. The internally created
> `httpx.Client` lives for the process; dropping the client discards the in-memory session. If you
> need deterministic cleanup or connection pooling, pass your own client and close it yourself.

Low-level auth helpers (rarely needed):

| Function | Purpose |
|---|---|
| `zero_api.auth.api.http_login(http, email, password)` | Full SRP login on a given `httpx.Client`. |
| `zero_api.auth.api.http_refresh(http, session)` | Refresh-token exchange without SRP. |
| `zero_api.auth.models.AuthSession.from_cookies(cookies)` | Build a session from a cookie jar. |

## API guide

All public methods hang off one `ZeroClient` instance. Methods are synchronous and return typed
Pydantic models. Account, position and order amounts are `Decimal`; market-data models such as
`InstrumentDetail` and `ChartBar` keep the wire's floats. Broker timestamps are timezone-aware
`datetime`s; display-date fields that the broker sends as text stay strings.

### Cash account

```python
account = client.account.get()
print(account.buying_power, account.buying_power_without_credit)
print(account.transfer_power, account.account_iban, account.overdrawn)
print(account.next_closing_date)
```

`MoneyTransactionAccount` includes the account number, IBAN, intraday amount, buying/transfer
power (with and without credit), last imported saldo, debit interest and the next closing date.
If you need to exclude an available credit line, use `buying_power_without_credit` rather than
`buying_power`.

### Instruments

| Method | Returns | Notes |
|---|---|---|
| `search(query, *, size=10)` | `InstrumentSearchPage` | Free text: name, WKN (German security ID) or ISIN. |
| `get(isin)` | `Instrument` | Master record: `type` plus trading state, venues and fees. |
| `detail(isin)` | `InstrumentDetail` | Market data; one model with optional fields (the wire has no class discriminator). |
| `chart(isin, period=ChartPeriod.ONE_YEAR)` | `list[ChartBar]` | One series; internally fetches `detail()` first. |

```python
from zero_api.instrument.models import ChartPeriod, InstrumentType

page = client.instrument.search("Rheinmetall", size=5)
print(page.stats.total)  # every match, including those beyond the per-bucket cap
for asset_type, count in page.stats.hits.items():
    print(asset_type.value, count)
for hit in page.results:
    print(hit.isin, hit.wkn, hit.short_name, hit.type)

instrument = client.instrument.get("DE0007030009")
assert instrument.type is InstrumentType.STOCK
venue = next(v for v in instrument.trading_venues if v.default_venue)
print(instrument.short_name, instrument.tradable)          # e.g. "OPEN"
print(venue.mic_code, venue.trading_hours_start, venue.trading_hours_stop)
print(instrument.min_qty, instrument.qty_incr, instrument.price_incr)

deriv = client.instrument.get("DE000UBS0JQ8")
if deriv.type is InstrumentType.DERIVATIVE:
    print(deriv.underlying, deriv.option_type, deriv.disabled_exec_types)

bars = client.instrument.chart(instrument.isin, ChartPeriod.ONE_MONTH)
print(bars[-1].date, bars[-1].close)
```

Things worth knowing:

- `get()` always returns the single `Instrument` model. `instrument.type` is an `InstrumentType`
  (`AKTIE`/`ETF`/`FONDS`/`DERIVAT`/`KRYPTO`); an ETC's *master* reports `ETF`, even though its
  search hit says `ETC` (`InstrumentSearchType` is the six-value search-hit vocabulary). Fields
  that can be null for some class are Optional: crypto `wkn`, ETF/fund `short_name`/`symbol`,
  non-derivative `underlying`/`option_type`, fund/derivative/crypto `ranking`/`sentiment`.
- `InstrumentDetail.symbol` is actually the WKN, despite the field name; crypto details omit it.
- `mic_code` is the venue's ISO 10383 Market Identifier Code. Use a value from
  `instrument.trading_venues`.

### Portfolio

```python
positions = client.portfolio.get_positions()
for position in positions:
    print(
        position.isin,
        position.quantity,
        position.available_quantity,
        position.avg_entry_quote,
        position.quote,
    )
```

`Position` carries the latest quote, the FIFO entry quote (`entry_quote_fifo`) and the edited
average entry quote. Quotes are informational; they are not a guaranteed executable price.
`Position` does not include a currency: the quote currency comes from the instrument master
(`Instrument.quote_currency_code`), so do not sum position values across instruments without
checking it.

> **Pagination:** `get_positions()` returns only the first page. No page/size parameters were
> ever observed, and `hasNext` was never seen `true`. If it ever becomes true for a very large
> depot, the extra positions are not fetched.

### Watchlist

| Method | Returns | Notes |
|---|---|---|
| `get()` | `list[WatchlistEntry]` | Instrument + watchlist metadata (order, added time, quotes). |
| `add_to_watchlist(isin)` | `WatchlistEntry` | Server prepends the entry with `wlOrder = 0`. |
| `remove_from_watchlist(isin)` | `None` | Keyed by ISIN, not by item id. |

```python
client.watchlist.add_to_watchlist("DE0007030009")
for entry in client.watchlist.get():
    print(entry.watchlist_item.wl_order, entry.instrument.short_name, entry.watchlist_item.bid)
client.watchlist.remove_from_watchlist("DE0007030009")
```

### Orders

| Method | Returns | Notes |
|---|---|---|
| `list(status="Open", *, page=0, size=1000)` | `OrdersPage` | `status` is the history tab: `"Open"`, `"Canceled"` or `"Filled"`. |
| `place(order, *, device=None)` | `Order` | Runs 2FA, verify, ex-ante document, then places the order. |
| `cancel(order_id, *, device=None)` | `Order` | Uses the string `order_id`, not the numeric `id`. |

`OrdersPage` has `.list` and `.has_next`. The default page size of 1000 covers most accounts; if
`has_next` is true, request the next page explicitly.

`Order.status` is an `OrderStatus` enum — compare it to enum members such as
`OrderStatus.FILLED`. The values you will normally see:

| Status | Meaning |
|---|---|
| `Created` | Accepted by the broker, not yet working at the venue |
| `Open` | Working at the venue |
| `Filled` / `PartiallyFilled_Filled` | Executed (the latter after a partial fill) |
| `Canceled` | Canceled |
| `Rejected` | Rejected by the broker or venue |
| `Expired` | Expired without executing |
| `Open_CreatedCancel`, … | Cancellation requested / in progress |

There are also `PartiallyFilled` variants with the same cancellation suffixes; `OrderStatus` lists
the full set.

#### Placing an order

> **This places a real order with real money.** There is no paper-trading or dry-run mode. The
> library does not validate lot sizes, price increments, trading hours or buying power — the
> broker does, and rejects what it does not like (usually as a `BrokerApiError`). Before building
> an order, check `instrument.tradable`, `instrument.disabled_exec_types`, `instrument.min_qty`,
> `instrument.qty_incr`, `instrument.price_incr`, `instrument.minimum_order_value` and the
> selected venue's trading hours.

Build an `OrderRequest` from the instrument's own trading rules:

```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from zero_api.ordering.models import ExecType, OrderRequest

instrument = client.instrument.get("DE0007030009")
venue = next(v for v in instrument.trading_venues if v.default_venue)

order = OrderRequest(
    isin=instrument.isin,
    direction="buy",                 # "buy" or "sell"
    exec_type=ExecType.LIMIT,
    quantity=Decimal("1"),           # respect instrument.min_qty / qty_incr
    mic_code=venue.mic_code,         # target venue, from trading_venues
    limit_price=Decimal("900.00"),   # example only; use instrument.price_incr steps
    valid_to_date=datetime.now(UTC) + timedelta(days=30),
)

placed = client.orders.place(order)  # approve the 2FA push on your device
print(placed.order_id, placed.status)  # "Created" — not filled yet
```

Supported `ExecType` values and their price fields:

| `ExecType` | Placeable | Fields |
|---|---|---|
| `MARKET` | ✅ | `quantity`, `mic_code` |
| `LIMIT` | ✅ | `limit_price` |
| `STOP_LIMIT` | ✅ | `limit_price`, `stop_price` |
| `TSL` | ✅ | `stop_price` plus `trailing_price_pct` or `trailing_price_abs` |
| `OCO` | ✅ | `limit_price`, `stop_price` |
| `stop` (plain stop-loss) | ❌ | history only |
| `savings`, `fraction`, `previously_quoted` | ❌ | history only |

`TSL` is a trailing stop-loss; `OCO` is one-cancels-the-other.

`valid_to_date=None` means "now" (what the app sends for market orders). Naive datetimes are
treated as UTC. `quantity` and all prices are serialized as JSON numbers because that is what the
broker accepts, even though you pass `Decimal`s. Prices are in the instrument's quote currency
(`instrument.quote_currency_code`), not necessarily EUR.

`place()` performs the site's full submit sequence:

1. Probes the 2FA unlock and unlocks first if needed (see below).
2. `POST /api/trading/order/verify` — precheck.
3. `POST /apic/exante` — generates the mandatory ex-ante cost document in the broker's
   Posteingang (inbox). **This is a real side effect**, even if the order is rejected afterwards.
4. `POST /api/trading/order` — the actual order.

It returns as soon as the broker accepts the order (`status="Created"`). It does **not** wait for
a fill; poll the order lists for that:

```python
for page_status in ("Open", "Filled", "Canceled"):
    page = client.orders.list(page_status)
    for order in page.list:
        print(page_status, order.order_id, order.status, order.executed_quantity, order.exec_price)
```

Market orders are only accepted during the chosen venue's trading session. Outside those hours,
the broker answers `406` with
`errorCode="ORDER_TYPE_NOT_ALLOWED_CHEAP_ORDER_OUTSIDE_SESSION_WINDOW"`. That is a broker error,
not a client bug.

#### Cancelling an order

```python
page = client.orders.list("Open")
for order in page.list:
    print(order.order_id, order.isin, order.status, order.quantity)

# client.orders.cancel(order.order_id)  # also needs the 2FA session unlocked
```

`cancel()` returns the order in a transition state (for example `"Open_CreatedCancel"`); the final
state is not immediate. Poll the order lists until the order leaves `Open`.

### 2FA order unlock

Order execution is protected by an app push. The unlock is **server-side session state** tied to
the cookie session — no extra token is stored by the client.

You normally do not call this slice yourself: `orders.place()` and `orders.cancel()` probe
`is_authorized()` and unlock automatically when needed. Approve the push in the ZERO app; the call
blocks until then (60 seconds by default, polling every 2 seconds).

```python
if not client.sessiontan.is_authorized():
    devices = client.sessiontan.enrolled_devices()
    print([(d.serial, d.customer_device_name) for d in devices])
    client.sessiontan.unlock(devices[0], timeout=120)
```

**Multiple enrolled devices:** the client refuses to guess. Set a default once — in the
constructor (`ZeroClient(..., default_device="<serial>")`) or at runtime — or pass a device to each
call:

```python
client.sessiontan.default_device = devices[0]  # str serial or EnrolledDevice
client.orders.place(order, device=devices[0])
```

`unlock()` raises `TwoFactorError` on timeout, on a declined/unknown state, or when several
devices are enrolled and none was chosen. Only `AUTHORIZATION_INITIAL` (pending) and
`AUTHORIZATION_USER_ACCEPTED` (approved) have been observed; any other state fails loudly rather
than being guessed. If you need a longer wait than `place()`'s built-in 60 seconds, pre-unlock
with `client.sessiontan.unlock(..., timeout=...)` and then place the order.

| Method | Purpose |
|---|---|
| `is_authorized()` | Is this session unlocked for order execution? |
| `enrolled_devices()` | Devices that can receive the push. |
| `unlock(device=None, *, timeout=60.0, poll_interval=2.0)` | Full flow: choose device, push, poll, verify. |
| `request_auth(serial)` / `auth_state(handle)` / `abort(handle)` | Low-level primitives behind the flow; normally use `unlock()`. |

The unlock is bound to the cookie session and has been observed to persist for hours, including
across browser restarts on the same cookies. A fresh login may require a new approval.

## Error handling

All library-specific exceptions derive from `ZeroApiError`:

| Exception | Raised when | Useful attributes |
|---|---|---|
| `ZeroApiError` | Base class for the library's own errors. | — |
| `AuthenticationError` | Login/refresh failed, or no login method is configured. | — |
| `TwoFactorError` | The 2FA flow could not be completed: timeout, unknown/declined state, ambiguous device, or an unexpected 2FA response. | — |
| `BrokerApiError` | Non-2xx response with the broker's `{"type": "apiError", ...}` body. | `status`, `message`, `error_code`, `errors` |

```python
from zero_api.core.exceptions import BrokerApiError, TwoFactorError, ZeroApiError

try:
    client.orders.place(order)
except BrokerApiError as exc:
    print(exc.status, exc.error_code, exc.message, exc.errors)
except TwoFactorError as exc:
    print("2FA not completed:", exc)
except ZeroApiError as exc:
    print("zero-api error:", exc)
```

Other failures are intentionally **not** wrapped:

- `pydantic.ValidationError` — the broker's response no longer matches the recorded schema. This
  is the loud drift signal; upgrade the library or open an issue with the endpoint and error.
- `httpx.HTTPStatusError` — a non-2xx response that is not the broker's `apiError` shape.
- `httpx.HTTPError` — network, timeout and transport errors.
- `ValueError` — an unexpected success body where the client expected an explicit confirmation
  (for example a watchlist removal that did not answer `true`).

## Project status

**Alpha (0.1.0).**

Known unverified corners, deliberately left out:

- Some execution types appear only in order history (`savings`, `fraction`, `stop`,
  `previously_quoted`); the client types them but cannot place them.
- Declined/expired 2FA states were never captured; unknown values raise `TwoFactorError`.
- Position pagination has never been observed to kick in.

## Limitations and gotchas

- **Private endpoints, no contract.** The broker can change a payload silently. The strict
  Pydantic models are designed to turn that into a visible error, not to make it impossible.
- **No retries beyond one re-auth.** Rate limiting, backoff and idempotency are your
  responsibility. In particular, do not blindly retry `place()` after a timeout — the order may
  already exist. Check the order lists first.
- **Sessions do not survive a process.** Cookies live only in memory; persist nothing unless you
  build that yourself and protect it accordingly.
- **No async API.** Run the synchronous calls in a thread if you need them inside an event loop.
- **No `close()` or `logout()`.** Pass your own `httpx.Client` if you need explicit lifetime
  management; discard the client to drop the session.
- **`place()` is blocking and side-effecting.** It can wait up to 60 seconds for a 2FA approval
  and creates an ex-ante cost document before submitting the order.
- **The library won't save you from a bad order.** It forwards what you build. Validate quantity
  increments, price ticks, market hours and buying power against `Instrument` and
  `MoneyTransactionAccount` first.
- **No sandbox.** Every authenticated call goes to the real broker.

## Development

```bash
uv sync
uv run mypy .      # strict, over src/ and tests/
uv run pytest      # offline suite, no broker access
```

Run a single test:

```bash
uv run pytest tests/ordering/test_ordering.py -k place
```

The suite replays **real, sanitized broker payloads** from `tests/<slice>/fixtures/` through
`httpx.MockTransport`. Do not replace them with hand-written approximations, and never add a test
that calls the live broker.

The release process is documented in
[RELEASING.md](https://github.com/wtfuii/zero-api/blob/main/RELEASING.md).

Project layout:

```text
src/zero_api/
├── client.py        # ZeroClient — the client object
├── core/            # shared HTTP session, headers, exceptions
├── auth/            # Cognito SRP login + token refresh
├── account/         # cash account
├── instrument/      # search, master data, detail, charts
├── portfolio/       # depot positions
├── watchlist/       # watchlist read/add/remove
├── ordering/        # orders: list, place, cancel
└── sessiontan/      # app-push 2FA unlock
tests/               # offline tests + sanitized fixtures, mirrors the slices
scripts/             # developer tools (live smoke test)
```

## Live smoke test

[live_smoke.py](https://github.com/wtfuii/zero-api/blob/main/scripts/live_smoke.py) is a developer
tool that talks to the real broker. It is **not** part of the test suite.

```bash
export ZERO_EMAIL="you@example.com"
export ZERO_PASSWORD="..."          # or let the script prompt
uv run python scripts/live_smoke.py --skip-mutations
```

Read-only checks cover login/account, instrument search/get/detail/chart, positions, watchlist
reads and order lists. By default the script also does a watchlist add/remove round-trip and
restores the list; `--skip-mutations` disables that.

`--trade-isin <ISIN>` opts into a **real-money** round-trip: one market buy, wait for the fill,
one market sell. Fees apply to both orders. The script asks for confirmation and refuses to sell
unless the buy is confirmed filled. Use `--help` for timeouts, device selection and MIC options.

## Contributing

Issues and pull requests are welcome. Before opening one:

1. Run `uv run mypy .` and `uv run pytest`.
2. Add or update the sanitized fixture in the slice's `tests/<slice>/fixtures/` and test against
   it.
3. Keep the vertical-slice structure: a slice owns its transport, models and client methods;
   shared HTTP/session code belongs in `core/`. Do not add a browser dependency to `src/`.

Never paste cookies, IdTokens, account numbers, device serials or raw HAR files into an issue or
commit. `captures/` and `docs/explorations/captures/` are gitignored for that reason.

## License

[MIT](https://github.com/wtfuii/zero-api/blob/main/LICENSE) © 2026 Klaus Deja.

This project is not affiliated with, endorsed by, or supported by Finanzen.net or Finanzen.net
ZERO. Automating a broker account may violate its terms of service; you are responsible for how
you use this library. Trading involves risk, and software bugs can cause financial loss.
