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
| **Husk og lektier** | The list of things to do (today view: focus day + 2 days, grouped by day, each task once on the first day `kTaskActive` says it applies – same as the kiosk, whose column is just "Husk") | Husk og frister |
| **Frist** | Only a due *date* ("frist torsdag"), never a category | |
| **Praktisk info** | Deviations and practical info: omlagt dag, vikar, lukkedag, skolefoto (briefing section too; in the today view a chip in the lanes' "Hele dagen" row) | Vigtig info, Særligt |
| **Feed** | The tab with Aula posts and albums; filters *Alle · Opslag · Billeder* | Alt |
| **Beskeder** / **Private samtaler** | Messages / the locked private threads | |
| **Familiekalenderen** | The family's Google calendar, everywhere in UI text (fixed word, not the configured name). Only a link that opens Google's site says "Åbn i Google Kalender" | Google Kalender (as name), kalenderen |
| **Føj til familiekalenderen** / **Tilføj** | Heading once / the action button and dialog submit | Opret i kalender, oprettes |
| **Datovælger** | The date picker inside the dialog | kalender |
| **Familieassistenten** | Writes the overview (AI); fallback text: "ud fra faste regler" | AI, Samlet automatisk, appens egne regler |
| **Overblik** | Only the assistant's day/week summary | (not for the app in general) |
| **Hele familien** | Everyone | Familien, Fælles |
| **Adgangskode** / **Kode til private samtaler** | Login / unlocking private threads | |
| **Indstillinger** (gear icon, header top-right on all sizes) | All actions (Kioskvisning, Sæt hjem for vejret, Log ud). The status line shows only status ("Opdateret …") – a progress ring always sits left of the gear (no spinner, nothing in the status line) – warnings live under ⚠ at the title | Menu, Opdatér nu (removed: the server fetches by itself) |

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
`private_messages.json`, `ai_last_invalid.json`), `web/family.json`, `web/briefing*.json`, `web/media/`, `web/server_state.json`,
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

- `test_calendar_ui.py` has 5 failures that predate the Oct 2026 desktop revamp: the tests still expect the configured
  calendar name ("Oprettes i Familiekalender") where the app shows the fixed term "familiekalenderen".

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
- `Client.generate_json(system, prompt, validate=…, cache_key=…, schema=…)` returns a dict or raises
  `AIUnavailable(reason)`. Callers catch it and use their rule-based fallback; they never let it escape.
- Always pass a `schema` (Gemini `responseSchema`, OpenAPI subset: `OBJECT`/`ARRAY`/`STRING`, `nullable`). Gemini
  must then return that shape; Claude ignores it (prompt-driven). `validate` still runs. If the provider rejects the
  schema with HTTP 400, the same request is repeated once without it (logged). Format examples in prompts must be
  valid JSON (no `…` placeholders outside strings) – the model copies them.
- Invalid answer (not JSON, or `validate` raises): retried once immediately; if the retry is also invalid, a pause is
  set for that content only (`invalid[<cache key>]` in `ai_usage.json`, 15 min doubling to 6 h, `INVALID_BACKOFF`).
  It never sets the global `backoff_until` – other content and other features keep working. A valid answer for the
  content removes its mark. A legacy global pause caused by `ugyldigt_svar` (no `pause_reason`) is ignored.
- Two reasons for a bad answer: `ugyldigt_svar` (not JSON / wrong shape from the provider) and `fejlede_tjek` (valid
  JSON, but the caller's `validate` raised – e.g. a time not in the data). Same retry and per-content pause; the
  mark stores its reason. Neither is shown in the note under the overview.
- Times in briefing text (`briefing._times`, also used by `calendar_ai.times_in_text`): `kl. 8`, `klokken 13`,
  `13.00`, `13:00`, plus the END of a range only when it hangs on such a time (`kl. 8-13`, `8.00–13`,
  `fra kl. 8 til 13`). Bare pairs (`side 12-20`, `13-14`) are not times. Applies to the data and to the model's
  narrative alike, so a range end in the narrative must also be in the data.
- Prompts ask for correct Danish (no Norwegian/Swedish words, everyday words, short sentences). Language is never
  validated – an odd sentence beats falling back to the rules; a bigger model is the fix if it keeps happening.
- The raw invalid answer (last one only) is written to `secrets/ai_last_invalid.json` (`Client(invalid_path=…)`,
  set by `briefing.ai_client`; 0600; in `DENY_NAMES`, .gitignore, .dockerignore and the pre-commit guard). It holds
  family data: never log the answer text, only the parse error and the file path.
- Reasons that send nothing (`mangler_noegle`, `ukendt_udbyder`, `pause`, `dagsbudget`, `minutgraense`, a waiting
  invalid mark) log one warning per state (`notice` in `ai_usage.json`), not one per fetch. `_fail` and `_ok` reset it.
- The briefing's fallback log line names the reason; during a pause it also names the last error and when it was
  (`briefing.why_unavailable`).
- Cache key = provider + model + system + (cache_key or prompt). Pass a `cache_key` that excludes volatile parts
  (the briefing uses its fingerprint, which ignores the "now" timestamp).
- Budget day = Pacific time (Google resets free-tier RPD at midnight Pacific). Requests are counted when sent.
  RPM: wait up to `max_wait_seconds`, else `minutgraense`. Provider errors (429/402/401/403/5xx/network) set a global
  backoff per reason (`BACKOFF`, stored with `pause_reason`), doubling per consecutive failure; a 429 on a per-day
  quota pauses until Pacific midnight.
- Briefing fallback states in `briefing*.json`: `ai_stale` (last AI briefing for the same period kept; data has
  changed since) and `ai_fallback` (made by `offline_briefing`). Both carry `{reason, since}`. The frontend shows a
  subtle note (`aiNote(b)`, `.ainote`) just below that briefing's text – one per briefing: under "I dag" for the
  day, under "Ugen" for the week; on the kiosk `#kAiNote` under the overview. Wording (user decision, keep it):
  "Familieassistenten er ikke tilgængelig – overblikket er lavet ud fra faste regler." / "… er fra kl. HH.MM og er
  måske ikke opdateret." No banner at the top any more (removed Oct 2026). The note never shows the technical
  reason; ⚠ at the title and `/api/status` do (login only, never the key).
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
- `/api/status` → `weather: {enabled, home}` (yes/no only). When enabled and home is missing, the ⚠ dialog lists
  "Hjem for vejret er ikke sat" with a button; the action itself is also "Sæt hjem for vejret" in the menu.
- Weather is an extra: any failure → no weather, never an error banner (it is listed under ⚠). Tests use a simulated MET Norway only.

## Aula fetch (optimised, Oct 2026)

- All Aula calls in one fetch go through `AulaGate`: one shared cap on simultaneous calls (`max_concurrent`, 3),
  a timeout per call (`request_timeout`), and `max_retries` retries with growing pauses (or `Retry-After`, max 30 s)
  for temporary errors only (timeout, network, 429/5xx). Never for login or program errors. Logs strip URL query
  strings (`_describe`). `_once` runs shared work once per fetch even when several parts ask at the same time.
- The parts (tasks, weekplan, posts, messages, albums) run concurrently, each with `part_timeout`; a failing part
  keeps its previous data (`None`) and is listed under ⚠ as `aula.part:<key>` until it works again.
- Weather (MET Norway) does not depend on the other data, so `run_once` starts `_fetch_weather` in a thread
  (`asyncio.to_thread`) right after `progress.start` and awaits it just before writing family.json.
- Messages are incremental: a thread whose list entry is unchanged (`sig` = `_thread_sig`) is reused without calls,
  and the list stops after `messages_stop_after_unchanged` unchanged threads in a row; the rest is carried over.
- Deep check (user decision: every hour, `deep_check_minutes`, `health.aula.last_full_sweep`): the whole list is read
  AND every thread is fetched again (no reuse), because an edit to an older message does not always change the
  thread's list entry. Images are cached on disk by id, so the deep check costs message calls, not downloads.
- Not yet run against the real Aula at the time of writing (only simulated in `tests/test_aula_fetch.py`).

## Day view heading

The visible "I dag / fredag 9. oktober" heading above the overview is gone (user decision, Oct 2026: the
overview's own heading is enough, also in the evening when the view shows tomorrow). `.dayhead` stays in the DOM as
`.sronly` so screen readers and `aria-labelledby="dayTitle"` keep working.

## Weather in the messages view

The header weather folds out on every page, including Beskeder (a `body.mode-mail #wxHeadPanel{display:none}` rule
used to hide it); toggling it in the messages view calls `sizeMail()` so the split view still fits the screen.

## Messages view layout

Beskeder uses the same `.wrap` width and header as every other view on tablet/desktop (user decision, Oct 2026:
it used to be 1680px wide with a smaller title and no date line, which made the page jump when switching views).
Only `padding-bottom` is tighter so the split view fits the screen; `sizeMail()` sizes the panes from where the
header ends. On phones (`max-width:699px`) the header is still hidden in Beskeder to give room to the messages.

## Progress while fetching (`progress.py`, Oct 2026)

User decisions (revised): a progress ring left of the gear (`#progWrap`, `position:absolute` in the header so
nothing else in the layout moves) shows the overall progress, weighted with the parts' own percentages (done or
failed = 1, waiting = 0, running with a total = done/total, running without = 0.5). Red if any part failed; ✓ for
~4 s after the fetch (`PROG_LINGER`), then a calm full ring (`.idle`, muted; red if the last fetch had a failure).
The ring is ALWAYS there when the app runs on the server (user decision, replaces "hidden when idle"); without a
server there is none. Tapping it opens `#progPop` (same menu style as the gear) with one bar per data type – during a
fetch the live bars, otherwise the last fetch ("Seneste hentning kl. HH.MM · 52,3 s"). Each finished or failed part
shows its time (`ms` from `progress.py`; format: under 1 s in ms, else seconds with one decimal: "820 ms", "45,2 s");
running parts show no time. Each row is a grid with fixed columns (name · bar · status · time), so the bars sit
in the same place whatever the state; the popup is 340 px (full width minus a margin on phones). Esc/outside tap closes the popup; if it was open while a fetch ended, it closes ~4 s
later by itself; opened later to look at the last fetch, it stays. "Tving fuld hentning" is a link at the bottom of
the popup (a `<button>` styled as a link; grey and disabled while fetching, no confirmation): POST `/api/refresh?full=true` (CSRF header) → `Runner.trigger(full=True)` →
`run_once(full=True)`: Aula deep check of all threads + `make_briefing(force=True)` → `generate_json(fresh=True)`
(skips the AI cache; pause and daily budget still apply). The popup stays open and follows the fetch.
The bars themselves (from the first version) are unchanged: one per data type the fetch actually
runs (Google, Aula-kalender, Opgaver, Ugeplan, Opslag, Beskeder, Billeder, Kalenderforslag, Vejr, Overblik –
parts switched off in config get no bar, `fetch_family._progress_parts`). Real percentage where the total is
known (Google: calendars, Beskeder: threads to fetch once the list is read, Billeder: albums, Overblik: day +
week), otherwise a "henter …" animation (static with reduced motion). A failed part turns red ("fejlede") until
the fetch ends; afterwards the bars stay ~4 s as "færdig" (`PROG_LINGER`) and disappear. Never on the kiosk.
- `progress.start(keys)` / `begin(key, total)` / `step(key, done, total)` / `finish(key, ok)` / `end()`; in memory
  only, reset in `create_app`. `end()` marks parts that never finished as failed (timeout, login required …);
  `run_once` and `Runner.run` both call it. `/api/status` → `progress: {running, started, finished, parts}`.
- Frontend: `renderProgress()` (ring + popover), `progOverall()`, `progItem()`; `watchRun` polls `/api/status` every
  1.5 s while fetching and only redraws the ring, not the whole app. Each bar is `role="progressbar"` with
  `aria-valuetext`; the ring button's `aria-label` carries the overall percentage.

## Problems at the title (`problems.py`, Oct 2026)

User decisions: any error in AI, Aula, Google (read and write), weather or ntfy shows ⚠ + count next to the title
"Familieplan"; tapping opens `#probDlg` with the full message. Never on the kiosk (the kiosk keeps `#kStatus`).
An entry disappears the next time that integration succeeds, or on restart. The old red status-line warnings
are gone; "Data er over en time gamle" and "Hjem for vejret er ikke sat" moved into the dialog. The AI note
under each overview stays (it explains how the overview was made).
- `problems.report(key, area, title, detail, hint, action)` / `clear(key)`; in-memory only, `create_app` calls
  `reset()` (and tests reset via an autouse fixture). `detail` goes through `scrub()` (keys, tokens, bearer,
  lat/lon); `action` may only be a relative link inside the app (e.g. `auth`).
- Keys: `ai.briefing.day|week`, `ai.briefing.error`, `ai.calendar`, `aula.fetch`, `aula.part:<key>`, `aula.mark` (derived from
  `State.mark_error` in `/api/status`), `google.read:<name>`, `google.api:<name>`, `google.write`,
  `google.write.setup`, `weather`, `ntfy`. AI: every overview or calendar check made without AI counts,
  whatever the reason; offline mode in config does not. Hints come from `ai.HINTS` (shared with the self-test).
- Never family data in a problem: no AI answer text (only that `secrets/ai_last_invalid.json` exists), no
  coordinates. `/api/status` (and thus `problems`) is only available after login.
- Frontend: `currentProblems()` = server list + client-side (old data, missing home; without a server also
  `health.google` from family.json). `renderProblems()` draws the button in `#probSlot`; `fillProblems()` the dialog.

## Kiosk (`?kiosk=1`)

User decisions (Oct 2026): one screen designed for an iPad in landscape (portrait just gets the same layout,
denser – no separate design). Small, light type (people stand close to it), thin rules instead of filled cards, the
system font (San Francisco on iPad; nothing to load, so the shared font `<link>` is untouched).

- Theme: the app-wide theme (see "Theme" below); the kiosk only adds its own details (`body.kiosk:not(.night)` for
  the current row and the warning colours).

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

## Theme (whole app, Oct 2026)

User decisions: the kiosk's weather themes for every non-kiosk device too (desktop, tablet, phone); dark 21–06 like
the kiosk (the switch to tomorrow stays at `evening_hour`); the weather theme replaces the device's light/dark
setting entirely (no `prefers-color-scheme`, no `data-theme`); a week without forecast keeps today's sky; feed and
messages get styling only.

- `applyTheme(now, day)` runs at the start of every `render()` and every 20 s (the auto-offset interval). `body[data-sky]`
  is the sky of the focus day's weather, mapped from the day icon (`SKY`: sol, skyet, overskyet, regn, frost; thunder →
  regn, snow → frost, fog → overskyet), fixed all day. No weather: sage (the `body` defaults). `NIGHT_FROM`–`NIGHT_TO`
  (21–06) adds `body.night`: dark tokens, no sky. `data-sky` is kept at night.
- Tokens live on `body`: `--sky1..3`, `--ink`, `--muted`, `--line`, `--accent`, `--paper` (= `--sky2`), `--surface`
  (translucent white over the sky), `--solid` (dialogs, menus, the date picker – never translucent), `--today`,
  `--know`, `--warn`, `--rain`. Every sky keeps `--ink` ≥ 7:1 and `--muted`, `--accent`, `--warn`, `--rain` ≥ 4.5:1
  against its darkest and lightest point (tested in `test_kiosk_layout.py`).
- The single `theme-color` meta follows the top of the screen (`--sky1`, or `--paper` at night) in and out of the kiosk.
- Font: the system font everywhere (`--display`/`--body`); no web fonts are loaded by `index.html` (the login and
  `/auth` pages in `server.py` still name Atkinson/Bricolage with system fallbacks).

## I dag (today view, Oct 2026)

User decisions: a shared time axis with one lane per person, 07–21 that extends when needed, overlapping things for
one person split the lane side by side, iPad portrait keeps (narrower) lanes, phones get a list.

- `renderToday` → `renderBrief` (clamped to 4 lines by `clampBrief`, "Vis hele overblikket" toggles `state.briefOpen`),
  `renderDayLanes`, `renderDayHusk`. Lanes in `kPeopleOrder()` minus hidden people.
- `dayLane(p, day)`: lessons (`lessonsOf`) as `kind:"lesson"`; school blocks without lessons as `"school"`; family events
  (`people: ["family"]`) as `"fam"` in every lane; other events `"ev"`; events covering the whole day and
  `allDay` events plus `dayPlans()` (same filter as the old Praktisk info) in the "Hele dagen" row. `endInferred`
  events are points (start only, `NO_END` in the label).
- `layoutLane`: overlap groups → columns (`col`/`ncol`), using a visual minimum of `BLK_MIN` (25 min). A family
  event is drawn once across all lanes (`.dspan`) only if it has `ncol === 1` in every lane, otherwise per lane.
- Positions are percentages of the axis (`--hours` × `--hh`). The now line (`.dnow`) only when the focus day is today;
  a 60 s interval redraws the lanes (not the brief/husk), skipped while a dialog is open.
- Taps go through `dItems` (`data-d` keys, delegated click on `#todayView`): `showEvent`, `showPlan`, or `openDetail`
  with `lessonDetailHtml` (shared with the kiosk's lesson detail).
- `< 700 px`: `.dgrid` hidden, `.dlist` shown (rendered together; CSS decides). One row per event (people merged),
  school as one row per child, a "Nu" marker.
- `renderDayHusk`: `.hgroup[data-day]` per day, tasks are `[data-task]` buttons → `showTask`.

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
