# AGENTS.md

## What this is

`zero-api` — a Python library (not an app) providing a client for the German broker **Finanzen.net ZERO**. There is no official API: the library mimics the broker's private HTTP endpoints over plain `httpx`. It never drives a browser at runtime — no Playwright dependency. Login page: `https://mein.finanzen-zero.net` — start explorations and traffic capture there.

## Non-negotiable conventions

- **Strong typing everywhere**: full annotations, `mypy --strict` must pass; no untyped public API. Use Pydantic models or dataclasses for request/response shapes, never bare dicts.
- **Vertical slice architecture**: features are self-contained vertical slices (e.g. `auth/`, `portfolio/`, `ordering/`), each owning its transport, models, and public client methods. No cross-slice layer like a shared "services" or "utils" package; genuinely shared code (HTTP session, auth cookies) lives in `core/`.
- Library consumers get one top-level client object; slice internals are not public API.

## Transport strategy

- Default: **HTTP-first** — reverse-engineer the site's XHR endpoints (inspect network traffic) and mimic them.
- **Playwright is a development tool, not a runtime fallback.** It is used only by throwaway recorder scripts in `captures/` (gitignored, not installed by `uv sync`) to capture traffic during an exploration. If an endpoint looks impossible to mimic, the answer is a recorded exploration, not a browser dependency in `src/`. Login is proof of this: Cognito SRP runs over HTTP in `src/zero_api/auth/srp.py`.
- Before building a slice, **run an exploration first**: use the `exploration` skill to capture the real requests/responses for the feature, record findings (endpoint, headers, payload shape, session/CSRF handling) in the slice or `docs/explorations/`, then implement against them. Do not guess endpoint shapes.
- Treat captured traffic as reference data, not as stable contract — the broker can change it silently.

## Testing

- **No live broker calls in tests, ever.** Unit tests run against recorded traffic — real captured payloads in each slice's `fixtures/`, served through `httpx.MockTransport`; the network is faked in tests, never real.
- Tests mirror the slices: a slice's transport, models, and client methods are tested within that slice's own test package — no cross-slice shared test helpers beyond `tests/conftest.py`.
- Response fixtures must be real captured payloads (sanitized: no cookies/tokens/account IDs), not hand-written approximations — the point is to catch drift from the actual API.
- Typed test code too: tests run under the same strict mypy.

## Tooling (chosen setup)

- Python **3.13+**, managed with **uv** (`uv sync`, `uv add <pkg>`, `uv run <cmd>`).
- Verify with: `uv run mypy .` then `uv run pytest` (strict mypy first, then tests).
- Single test: `uv run pytest tests/path/test_x.py -k name`.
- Runtime dependencies are `httpx` and `pydantic` only, so `uv sync` installs nothing else — no browser, no Chromium. Playwright, if needed for a local recorder script under `captures/`, is installed ad hoc and never added to `pyproject.toml`.

## Status

Scaffolding and slices exist: `auth`, `account`, `instrument`, `portfolio`, `watchlist`, `ordering`, `sessiontan`, with `core/` holding the shared HTTP session and exceptions. The conventions above were set by the owner. If code and this file disagree, fix the code or update this file — do not silently drift.
