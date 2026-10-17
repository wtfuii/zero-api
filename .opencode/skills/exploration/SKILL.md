---
name: exploration
description: Guide for exploring Finanzen.net ZERO's private endpoints before implementing any slice or client method. Use this skill whenever a task involves adding or extending a slice (auth, portfolio, ordering, ...), reverse-engineering a broker endpoint, capturing network traffic, or the user says "explore", "capture", or "figure out how the site does X". Run the exploration BEFORE writing client code — do not guess endpoint shapes.
---

# Endpoint exploration

There is no official API for Finanzen.net ZERO. Every slice of this library mimics
private HTTP endpoints observed in the wild. An **exploration** is the act of
capturing that real traffic and recording what was learned — and it must happen
*before* implementation. Guessing endpoint shapes produces code that looks right
and fails at runtime; a recorded exchange is ground truth.

The deliverable of an exploration is a markdown finding in `docs/explorations/`
plus a sanitized capture file. Implementation comes after, against the findings.

## Workflow

### 1. Scope it

Answer in two sentences before touching tools:

- **Feature**: which user-visible action produces the traffic (e.g. "view portfolio positions", "place a limit order")?
- **Expected slice**: which vertical slice this will land in (`auth/`, `portfolio/`, `ordering/`, ...).

If the feature maps to more than one user action, explore them one at a time.

### 2. Capture with Playwright

Write a throwaway Python Playwright script per exploration. A minimal recorder:

```python
import json, os
from playwright.sync_api import sync_playwright

EXCHANGES = []  # (url, method, request_headers, post_data, status, response_body)

def on_response(resp):
    req = resp.request
    if req.resource_type != "xhr":  # the site's API calls are XHRs; ignore assets
        return
    EXCHANGES.append({
        "url": resp.url,
        "method": req.method,
        "request_headers": {k: v for k, v in req.all_headers().items()},
        "post_data": req.post_data,
        "status": resp.status,
        "response_body": resp.text(),
    })

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)  # visible: you interact with the site
    page = browser.new_page(record_har_path="captures/raw.har")  # HAR as backup
    page.on("response", on_response)
    page.goto("https://www.finanzen.net/zero")
    # Log in — if credentials come from the user, read them from env vars, never hardcode.
    # Then perform the scoped action manually (headless=False) or drive it in the script.
    page.pause()  # waits for you; close the browser when done
    browser.close()

with open("captures/<feature>.json", "w") as f:
    json.dump(EXCHANGES, f, indent=2)
```

Practical notes:

- The login flow and any bot protection is exactly why Playwright is the fallback
  transport; if even the browser flow is blocked, stop and record that finding.
- Keep `headless=False` and interact yourself — explorations are usually one-off,
  and watching the traffic appear beats scripting blind.
- Filter to the relevant origin/paths when writing the findings; the JSON capture
  can stay raw.

### 3. Sanitize before committing

Captures contain session cookies, auth tokens, and personal data. Never commit them raw:

- Strip `Cookie`, `Authorization`, `Set-Cookie`, and `X-CSRF-Token` header *values*
  (keep the header names — their presence matters).
- Replace account numbers, IBANs, names in bodies with placeholders.
- `captures/` is gitignored; only findings markdown is committed.

### 4. Record the finding

Create `docs/explorations/<feature>.md` using this template:

```markdown
# Exploration: <feature> (<date>)

## Scope
<what was explored, which user action, expected slice>

## Method
<how traffic was captured; anything notable about the flow, e.g. login redirects>

## Endpoints
### <METHOD> <path>
- Purpose:
- Request headers: (beyond standard; note CSRF/session handling)
- Request payload: (shape, with an example — sanitized)
- Response: (status, shape, with an example — sanitized)
- Auth/session: (cookies? bearer? CSRF token and where it comes from?)

## Open questions
<anything unclear or unverified>

## Capture
<relative path to the sanitized capture file, or "not retained">
```

Be honest in `Open questions` — an exploration with known gaps is useful; one
that silently guesses is not.

### 5. Hand off

Point the implementing slice at the finding. Per AGENTS.md, captured traffic is
**reference data, not a stable contract** — the broker can change it silently, so
the slice should fail loudly (validate response shapes, e.g. via Pydantic) rather
than misparse a changed schema.
