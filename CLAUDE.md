# CLAUDE.md – Familieplanner

Context for Claude (and any other developer) working in this repo. Read this first; keep it up to date when
architecture, conventions or status change. The user-facing documentation is README.md (in Danish).

## What this is

A self-hosted family planner for one Danish family (two parents, three children). It pulls school data from
Aula (calendar, timetable, weekly plans from Meebook, homework from Min Uddannelse, messages, posts, gallery)
and family events from Google Calendar (iCal), turns them into one overview, and serves it as a PWA to phones,
tablets and a kiosk screen. Events can be added to Google Calendar from any message, post or weekly-plan item via a service account (manually only).

## Language and conventions

- **All UI text, README, code comments, docstrings and commit-facing docs are Danish.** Keep it that way.
  This file and CHANGELOG.md are in English.
- **AI first, rules as silent fallback.** A language model (via `ai.py`) may be the primary source for
  summaries and – in later phases – extraction. The explicit rules (`homework.py`, `messages.py`, `activities.py`,
  `offline_briefing.py`) always stay as the fallback when the AI is unavailable, out of quota, or returns invalid
  output. Every AI feature must: go through `ai.py` (never call a provider directly), validate the output and fall
  back on anything unexpected, only receive scrubbed data (`briefing._scrub`), never receive private threads, and
  tell the user when the fallback is in use. Facts the AI returns must point back to a source (refs); unsourced
  points are dropped.
- **AI summary = narrative.** With AI, the day/week briefing is `fortaelling` (2–5 plain-text paragraphs, warm,
  chronological, addressed to the parents, ~150 words day / ≤220 week) plus `kilde_ids`. Validated in
  `briefing.validate_narrative`: valid source ids, every clock time must appear in the data, word limits, no
  formatting – otherwise fallback. The payload contains all calendar events (incl. place/note), messages and posts
  (non-private), tasks, week plans, weather, and from the timetable ONLY deviations (substitute, notes) – never the
  normal schedule (user decision: it's in the app). The rule-based fallback still produces `afsnit`.
- **Phase status of the AI work.** Phase 1 (done): `ai.py` + day/week summaries. Phase 2 (planned): homework per
  child extracted from messages, weekly plans and feed posts, with source quote and link. Phase 3 (done, `calendar_ai.py`): calendar
  suggestions only on AI-flagged items, date/time/place validated against the source text, changes only to exactly
  one app-created event, never written without a click; per item, `activities.find_all()` decides when the AI has
  no answer for it (unavailable, quota, reserve, older than 14 days on first run).
- **Time is an argument.** Code that depends on "now" takes the time as a parameter so tests don't depend on clocks
  (see `ops.py`). Keep that pattern.
- **Degrade, don't break.** If Aula or a Google calendar fails, the previous data for that source is kept and the UI
  shows a warning. A failing source must never produce an empty calendar.
- Python 3.14 is required (the `aula` client needs it). Frontend is plain HTML/CSS/JS, no build step.

## UI vocabulary (keep it consistent)

One concept, one word. New UI text uses exactly these terms; don't introduce synonyms.

| Term (Danish UI) | Means | Don't use |
|---|---|---|
| **Aftale** | A calendar event | aktivitet (in UI text), begivenhed |
| **Lektie / Husk / Skal gøres** | The three task kinds (`kind`: lektie, husk, handling) | opgave as a kind |
| **Husk og lektier** | The list of things to do (today view: focus day only, plus daily, open-ended and periods; the kiosk's column is just "Husk") | Husk og frister |
| **Frist** | Only a due *date* ("frist torsdag"), never a category | |
| **Praktisk info** | Deviations and practical info: omlagt dag, vikar, lukkedag, skolefoto (briefing section too) | Vigtig info, Særligt |
| **Feed** | The tab with Aula posts and albums; filters *Alle · Opslag · Billeder* | Alt |
| **Beskeder** / **Private samtaler** | Messages / the locked private threads | |
| **Familiekalenderen** | The family's Google calendar, everywhere in UI text (fixed word, not the configured name). Only a link that opens Google's site says "Åbn i Google Kalender" | Google Kalender (as name), kalenderen |
| **Føj til familiekalenderen** / **Tilføj** | Heading once / the action button and dialog submit | Opret i kalender, oprettes |
| **Datovælger** | The date picker inside the dialog | kalender |
| **Familieassistenten** | Writes the overview (AI); fallback text: "ud fra faste regler" | AI, Samlet automatisk, appens egne regler |
| **Overblik** | Only the assistant's day/week summary | (not for the app in general) |
| **Hele familien** | Everyone | Familien, Fælles |
| **Adgangskode** / **Kode til private samtaler** | Login / unlocking private threads | |
| **Indstillinger** (gear icon, header top-right on all sizes) | All actions (Kioskvisning, Sæt hjem for vejret, Log ud). The status line shows only status and warnings; a spinning sync icon while the server fetches | Menu, Opdatér nu (removed: the server fetches by itself) |

Formats: clock times `08.00` everywhere (kiosk clock too); dates `man 12/10` in lists, `mandag 12. oktober` in details.
No duplicates on one screen: a week-plan item that produced tasks (task id `<plan id>:<n>`) is shown only as a task
(`hasTasks` in `index.html`), in the today view and on the kiosk. The briefing may repeat items – it is a summary.

## Privacy rules (non-negotiable)

- Private message threads (parents ↔ school) are **never** written to `family.json`. Their content lives in
  `private_messages.json` next to the Aula tokens (outside the served folder) and is only returned by the server
  after a separate code (`FAMILIEPLAN_PRIVATE_CODE`). See `private.py`.
- Private threads are never sent to an LLM and never produce tasks or events.
- Everything sent to an LLM goes through `briefing._scrub` (CPR numbers, phone numbers, mail addresses).
  `familie_regler.md` is sent as written.
- **AI provider terms (user's informed choice):** Google Gemini **free tier**. Google may use free-tier input to
  improve its services; the terms address adult users while the summary is also shown on the kiosk for the children;
  EEA/consumer-use terms were discussed and accepted by the user. Keep the scrubbing strict for these reasons.
  The project's **billing must stay disabled** so the free tier can never incur cost. Moving to the paid tier or
  Claude is a config change (`[ai] provider/model/api_key_env`).
- `server.py` has `DENY_NAMES`: state, tokens and config must never be servable. Add any new state file there.
- Never commit config, tokens, fetched data or state files. `.gitignore` and the `no-family-data` pre-commit hook
  enforce this; if you add a new state file, add it to `.gitignore`, `.dockerignore`, the hook in
  `.pre-commit-config.yaml` and `DENY_NAMES`.
- Tests use invented data and simulated services only. Never write tests that touch real Aula, real Google
  Calendar or real family data.

## Architecture

One process (`server.py`, FastAPI + uvicorn) does three things: schedules fetching (every 15 min by day, less at
night), serves the app and data behind a shared family password (session cookie, CSRF, lockout), and offers an
`/auth` page for MitID login via QR code when the Aula login expires.

Data flow:

```
Aula (unofficial `aula` client) ─┐
Google Calendar (iCal)          ─┼─> fetch_family.py ─> analysis modules ─> web/family.json ─> web/index.html
familie_regler.md (free text)   ─┘                                     └─> private_messages.json (secrets/)
                         briefing.py ─> ai.py (Gemini/Claude) ─> web/briefing*.json
                                     └─> offline_briefing.py (fallback)
```

| Module | Responsibility |
|---|---|
| `server.py` | Web server, auth, API routes (`/api/...`), scheduler, MitID login page, `--selftest` entry |
| `fetch_family.py` | Fetching from Aula and Google, merging, assigning events to people, writing `family.json` |
| `homework.py` | Homework / "remember" / practical info from Meebook weekly plans |
| `messages.py` | Message analysis: which child, actions with deadlines, events, private threads |
| `activities.py` | Finds activities worth a calendar entry; cancellations and moves |
| `schedule.py` | Timetable cleanup: subject names, teacher abbreviations, hidden support lessons |
| `weather.py` | MET Norway weather (yr.no, Locationforecast 2.0 `/complete`, no key, User-Agent with contact): home location (rounded), hourly-cached fetch, coarse day summaries, rule-based advice |
| `calendar_ai.py` | AI calendar items from messages/posts/week plans → same `(suggestions, options)` shape as `activities.find_all`; per-item cache, batches of 8, ≤3 requests/fetch, 30-request reserve for the briefing |
| `ai.py` | The only way to call a language model: provider adapters (Gemini, Claude) over plain httpx, JSON output, content-hash cache, daily budget + RPM throttle, backoff, `AIUnavailable` |
| `briefing.py`, `offline_briefing.py` | Day/week overview ("Husk", "Skal gøres", "Praktisk info", "Kommende frister"); AI with rule-based fallback |
| `suggestions.py` | State of created/applied/dismissed calendar items and Google Calendar writes/reads (service account) |
| `private.py` | Storage and gating of private threads |
| `ops.py` | Push via ntfy, stale-data alerts, watchdog for background tasks |
| `selftest.py` | Checks a real setup end-to-end (Aula login, Google write, ntfy, permissions) |
| `web/index.html` | The whole frontend (~170 KB, inline CSS/JS). Contains demo data used when no server is present |

Runtime files (all git-ignored; in Docker they live in the `/data` volume):
`config.toml`, `.env`, `secrets/` (`aula_tokens.json`, `google_service_account.json`, `session.key`,
`private_messages.json`), `web/family.json`, `web/briefing*.json`, `web/media/`, `web/server_state.json`,
`web/suggestions_state.json`, `web/ai_cache.json`, `web/ai_usage.json`, `web/home_location.json`, `web/weather_cache.json`, and debug dumps `aula_dump.json`, `aula_feed.json`,
`weekplan.json`.

## Commands

```bash
# Environment (Python 3.14)
uv venv --python 3.14 .venv
uv pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install chromium        # for browser tests

# Tests – always run before committing
pytest                                       # everything
pytest --ignore=tests/browser                # server side only, fast (~20 s)

# Run locally
FAMILIEPLAN_PASSWORD=... python server.py --host 127.0.0.1 --port 8080
python server.py --selftest --no-notify      # against a real setup

# Pre-commit (once per clone)
pre-commit install
```

Dependencies: edit `requirements.in` / `requirements-dev.in`, then regenerate the locked files with the
`uv pip compile` commands in README ("Opdatér afhængigheder"). `aula` is pinned on purpose (unofficial client);
don't bump it without testing against the real service.

Root-level `activities_test.py`, `homework_test.py`, `messages_test.py` are manual debug scripts that run the
recognisers against the user's own dumped files. They are not part of pytest.

## CI

`.github/workflows/ci.yml` runs on every push to `main` and on pull requests: the full pytest suite including
Playwright browser tests and the axe-core accessibility test (`FAMILIEPLAN_REQUIRE_BROWSER=1` makes a missing
Chromium fail instead of skip), a Docker image build with an import smoke test, and a gitleaks scan of the full
history.

## Status (keep this section current)

Version: **v0.7.0** (first version in git; previously developed as zip files).

Known gaps and open work:

- **Weather has only been tested against a simulated MET Norway.** DMI was dropped 2026-10-03 (HTTP 400 on a wrong
  parameter name, then 429 "Server is busy"). Response shape from MET's Locationforecast 2.0 docs. First real
  check: the selftest lines *Hjem for vejret* and *Vejr (MET Norway)*.

- **AI (Phase 1) has only been tested against a simulated Gemini/Claude** (httpx `MockTransport`). The real
  endpoint, the model name `gemini-3.5-flash-lite`, Google's 429 detail format (`QuotaFailure` with a `PerDay`
  quotaId, `RetryInfo`) and JSON mode have not been exercised from this repo. First real check:
  `python server.py --selftest --no-notify` (sends one tiny request without family data). The user's real RPD/RPM
  values were not known when Phase 1 was written; defaults are `daily_cap = 100`, `rpm = 5`.

- Aula login, pagination, mark-as-read, Google Calendar writes and API reads have **only been tested against simulations**.
  The claim that event reminders only reach the service account (why `reminder_minutes` was removed) comes from
  Google's API docs and is unverified.
  First real run should be `python server.py --selftest`.
- The Docker image had never been built before CI was added.
- Homework is only fetched from Min Uddannelse; Meebook/EasyIQ homework not supported.
- README "Kom i gang" still describes the old `fetch_family.py` + `python -m http.server` flow; `server.py` is now
  the normal way to run it.
- `web/index.html` demo data uses the family's real first names; replace with invented names before the repo is
  ever made public.

Planned restructuring (do in small steps, tests green after each):

1. Move to `pyproject.toml` + `uv.lock` (replacing the four requirements files) and add a `justfile`
   (`setup`, `test`, `run`, `selftest`, `docker`).
2. Split `web/index.html` into separate CSS and ES-module JS files (no build step).
3. Move modules into a package (`src/familieplanner/`); move root debug scripts to `tools/`.
4. Split `fetch_family.py`, `server.py` and `activities.py` by responsibility.
5. Shorten README to overview + quick start; move detailed sections into `docs/`.

## AI conventions (`ai.py`)

- Config: `[ai] provider` (`gemini` default, `claude`), `model` (pinned, never `-latest`), `api_key_env`
  (`GEMINI_API_KEY`), `daily_cap`, `rpm`. Old `[assistant] mode = "claude"` maps to `provider = "claude"`.
  `[assistant] mode = "ai"` turns the AI summary on; without `mode` the code still defaults to `offline`.
- Auth: Gemini API key bound to a service account and restricted to the Generative Language API, sent as the
  `x-goog-api-key` header (never in the URL, never logged). No SDK, no ADC/OAuth. The Google Calendar service
  account is separate. Keys with spaces/non-ASCII are rejected as `mangler_noegle` rather than crashing.
- `Client.generate_json(system, prompt, validate=…, cache_key=…)` returns a dict or raises `AIUnavailable(reason)`.
  Callers catch it and use their rule-based fallback; they never let it escape.
- Cache key = provider + model + system + (cache_key or prompt). Pass a `cache_key` that excludes volatile parts
  (the briefing uses its fingerprint, which ignores the "now" timestamp).
- Budget day = Pacific time (Google resets free-tier RPD at midnight Pacific). Requests are counted when sent.
  RPM: wait up to `max_wait_seconds`, else `minutgraense`. Backoff per reason (`BACKOFF`), doubling per consecutive
  failure; a 429 on a per-day quota pauses until Pacific midnight.
- Briefing fallback states in `briefing*.json`: `ai_stale` (last AI briefing for the same period kept; data has
  changed since) and `ai_fallback` (made by `offline_briefing`). Both carry `{reason, since}`. The frontend shows
  the "AI ikke tilgængelig" banner (`#aiBanner`, top of `.wrap`, also in kiosk) for the briefing currently shown.
  The banner never shows the technical reason; `/api/status` → `ai` does (login only, never the key).
- Selftest uses `ignore_pause=True` so a fixed key can be verified immediately; it is not cached.
- Tests: only `httpx.MockTransport` with a fake clock/sleep. Never a real provider, never real family data.

## Weather conventions (`weather.py`)

- Source: MET Norway Locationforecast 2.0 `https://api.met.no/weatherapi/locationforecast/2.0/complete?lat=..&lon=..`
  (`complete` because `compact` has no gusts). Terms: User-Agent `Familieplan/1.0 <contact>` (default the repo URL,
  `[weather] contact` overrides; missing → 403), at most 2 decimals in coordinates, never ask before `Expires`,
  send `If-Modified-Since` (304 → reuse cached hours). Own limits: at most hourly, wait 30 min after an error,
  reuse ≤ 6 h old data. Cache carries `source` so an old DMI cache is never reused. Credit "MET Norway" (CC BY 4.0).
  Units: `air_temperature` °C, `next_1_hours.precipitation_amount` mm for the hour starting at `time`, wind and
  `wind_speed_of_gust` m/s, `cloud_area_fraction` % (stored 0–1). Only steps with `next_1_hours` are used (the
  6-hour tail is ignored). Hour icons come from `next_1_hours.summary.symbol_code` (`symbol_icon`), with the
  sun-elevation fallback when a code is missing or unknown.
- Home location: set once from the browser (`/api/home-location`, login required), rounded to 2 decimals, must be
  inside Denmark, stored only in `home_location.json`. Never in `family.json`, logs, or `config.toml`.
- `family.json` → `weather.dage`: coarse per-day summaries (whole degrees, rain class, part of day, wind class,
  frost, advice). Only days whose daytime (07–19) is covered. Coarse on purpose so the briefing fingerprint (and
  the AI quota) doesn't move with tiny forecast changes. The briefing gets them as `vejr` with `V` refs and a
  "Vejr" section; the kiosk shows `#kWeather` and leaves "Vejr" out of its overview band.
- Each day also carries `timer`: hours 06–22 (`kl`, `ikon`, `temp`, `regn` mm, `vind` m/s). For display only –
  `briefing.build_digest` picks its weather fields one by one and must never include `timer` (it would change the
  fingerprint every hour). Hour icons use a simple NOAA sun-elevation check (`sun_up`) so night hours get 🌙/☁
  instead of ☀. Emoji carry U+FE0F so Windows/Android draw them in colour.
- UI: the date heading (`#wxHead`) shows one chip (icon + min–max; tomorrow after the daytime is over) that folds
  out `#wxHeadPanel` hour by hour (open/closed in `state.wxOpen.head`). The kiosk's `#kWeather` shows the hour now,
  min–max, advice and a strip of the next ≤8 hours (tomorrow: odd hours from 07); a tap opens the full hours in the
  detail dialog. The strip loses hours from the end if the top bar is too wide. The strip scrolls sideways, never the page.
- Logging: `for_family` writes exactly one INFO line per run (off / home not set / no usable forecast / "N dage
  fra MET Norway (prognose hentet kl. HH:MM)"). Never coordinates in logs.
- `/api/status` → `weather: {enabled, home}` (yes/no only). When enabled and home is missing, the status line shows
  a warning button ("Hjem for vejret er ikke sat"); the action itself is "Sæt hjem for vejret" in the menu.
- Weather is an extra: any failure → no weather, never an error banner. Tests use a simulated MET Norway only.

## Kiosk (`?kiosk=1`)

User decisions (Oct 2026): one dark screen designed for an iPad in landscape (portrait just gets the same layout,
denser – no separate design). Only the kiosk is dark (`body.kiosk` redefines the colour tokens); the rest of the app
follows the device.

- Layout (`#kioskView`, grid rows): top bar (clock, day, status, weather, exit) → `#kBrief` overview band in full
  width → `.k-body`: `#kPeople` (one `.k-person` column per person: children, then adults, then anyone else; ~78 %
  of the width) + `#kHusk` (narrow).
- Columns: all-day events, then lessons and timed events in time order; family events (`people: ["family"]`) in every
  column; week-plan info (not lessons, not plans that produced tasks) for children. Tasks are only in `#kHusk`
  (no duplicates). The current lesson is `.now`, finished ones `.past`.
- `#kHusk`: focus day + 2 days. A task is listed once, on the first day `kTaskActive` (same rule as the today
  view's "Husk og lektier") says it applies.
- Overview: `fortaelling` joined into one text, max 4 lines; `kTrimBrief` cuts at a sentence boundary (split before
  an upper-case letter, so "kl. 17" is safe) with " …", or CSS line-clamps if even one sentence is too long. Without
  AI the rule points flow inline; without a fresh briefing it says so.
- Everything switches with `evening_hour` (focus day), incl. weather and the husk window.
- Fit: `fitKiosk` shrinks the font at most 2 px below the base (never under 14 px), then `kTrimBox` hides trailing
  rows behind "+N flere" (headings without rows are hidden too; headings and "Intet" don't count). Re-rendering on
  resize re-does the trimming.
- Taps: every tappable element has `data-k`; `kItems` maps it to a function that opens the `#detail` dialog via
  `kDetail` (closes after `K_DETAIL_MS` = 10 s or on a backdrop tap). Details only show what's already in the app –
  never a task's message text. `Esc` closes an open dialog first, then leaves the kiosk.

## Calendar and time conventions

- The app writes only to the calendar chosen by `suggestions.write_target()`; there is no "open Google" fallback.
- **No automatic suggestions.** `activities.find_all()` still finds activities (their titles are reused for the manual
  "Føj til familiekalenderen" options), but `family.json` `suggestions` only carries changes (`CHANGE_KINDS`: cancel, hold, move)
  of events already in the calendar. They are shown on the source message/post/weekly-plan item, not in a tab.
  Learned rules were removed; don't reintroduce a suggestions tab, badge or push without the user asking.
- "I dag" switches to tomorrow automatically after `evening_hour` (`dayOffset = autoOffset`); there is no manual toggle.
- An event's `end` is always set (the UI needs a slot), but `endInferred: true` means nobody gave an end time:
  show only the start. Never invent an end without setting the flag. Lessons default to `lesson_minutes` (45).
- All time display in `web/index.html` goes through `evRange()` / `lessonRange()`; don't format times inline.
- Text colour for links/accents is `var(--accent)`, never `#2F6FDE` (fails contrast).

## Working agreements

- Small, focused commits; one refactor per commit.
- Update `CHANGELOG.md` (under "Unreleased") for user-visible changes, and this file when status or architecture
  changes.
- Releases are git tags (`v0.8.0`, …), not zip files.
