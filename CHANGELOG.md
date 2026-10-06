# Changelog

All notable changes to this project. Versions are git tags. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Changed (kiosk)
- New kiosk layout: one dark screen for an iPad in landscape. Top bar with clock, day, status and weather (hour now,
  min–max, advice, a strip of the next hours); the overview in full width (max 4 lines, cut at a sentence with "…");
  one column per person – children first, then adults – with lessons and events in time order and family events in
  every column; a narrow "Husk" column for the focus day and the next two days. Replaces the per-child cards, the
  side cards ("Overblik"/"Husk og lektier" and "Dagens aftaler") and the fold-out weather hours.
- Tapping a lesson, event, task, the weather or the overview opens details that close after 10 seconds or on a tap
  outside. Columns that don't fit end with "+N flere" (the font shrinks at most 2 px first).
- Only the kiosk is dark; the rest of the app is unchanged.

### Fixed (kiosk)
- Kiosk tests expected the clock as `10:20`; the app shows `10.20` (the convention), so two tests failed.

### Changed (evening switch at 18:00)
- The default `[display] evening_hour` is now 18 instead of 17 everywhere: the today view, timetable, task list,
  overview window, the server's extra run at the switch, the evening push and the kiosk all follow it. An existing
  `config.toml` that sets `evening_hour = 17` keeps 17 – change it there.

### Changed (Husk og lektier)
- The today view's "Husk og lektier" only lists what is due on the focus day (today; after 17:00 tomorrow, like the
  timetable) instead of everything due in the next 7 days. Still listed: daily tasks, open-ended tasks ("snarest",
  a week from the message) and tasks with a period (start date → deadline) until the deadline. Tasks due later in the
  week are on their day in "Ugen" as before. The empty text says "Intet i dag"/"Intet i morgen". The overview's
  "Kommende frister" and the kiosk are unchanged.

### Changed (status line and settings)
- While the server fetches, the status line shows a spinning sync icon (static with reduced motion; screen readers
  hear "Henter data") instead of "Henter …". The app then checks `/api/status` every 3 seconds until the run is done
  and reloads the data at once, so the icon neither lingers nor is missed between the 5-minute reloads. Not on the kiosk.
- The action menu opens from a gear icon labelled "Indstillinger" instead of "Menu ⋯". The gear sits in the header's
  top-right corner on every screen size (it used to follow the status row and ended up on the left on phones); the
  status row stays bottom-right and only shows its warning line when there are warnings.

### Removed
- "Opdatér nu" in the app menu and on the Aula login page (the server fetches by itself every 15 minutes, and a
  MitID login starts a fetch right away). The `/api/refresh` endpoint is kept but no longer used by the UI.

### Removed (read-aloud, HelloFresh in the fetch script)
- Read-aloud of the overview is gone: the "Læs op" button, voice picker and speech synthesis in the app, and in the
  backend the `oplaesning` field and its rules in the prompt, `[assistant] speech` and its part of the cache key. A stray
  `oplaesning` in a model answer is dropped. This also fixes that `speech = false` never removed the field from the
  prompt (the strip string didn't match), so the model could spend tokens on it.
- `fetch_family.py` no longer tries to import the deleted `hellofresh` module (it logged "HelloFresh sprunget over" on
  every run) and `family.json` no longer has a `hellofresh` key. `[hellofresh]` removed from `config.example.toml`.

### Changed (week picker)
- The week picker shows the shown week as text ("Uge 41") between the arrows; the "Denne uge" button is gone.
  The week number in the board's corner stays.

### Changed (consistent UI terms, no duplicates)
- One word per concept, documented in CLAUDE.md → *UI vocabulary*. "Vigtig info" and the briefing section "Særligt" are
  now **Praktisk info**; the kiosk heading follows the briefing ("Overblik i dag") or falls back to **Husk og lektier**;
  the week legend says "Lektie, husk eller skal gøres".
- The calendar is **familiekalenderen** everywhere (source labels, status, warnings, dialogs). Adding is "Føj til
  familiekalenderen" (heading once) with "Tilføj" buttons; "Opret i kalender" is gone. The date picker is called
  *datovælger* so it isn't confused with the family calendar.
- The overview is written by **Familieassistenten**; the banner and footer no longer say "AI" / "Samlet automatisk".
- Status line shows status and warnings only. Actions (Kioskvisning, Opdatér nu, Sæt hjem for vejret, Log ud) moved
  to **Menu ⋯**. Missing home location shows as a warning link in the status line.
- Feed filter "Alt" → "Alle" (as in Beskeder). Week board lane "Familien/Fælles" → "Hele familien". Kiosk clock
  uses `08.00` like the rest of the app; task list dates use `man 12/10`. Aula login page button "Opdatér nu".
- Private-threads dialog label "Kode til private samtaler" (distinct from the login password).

### Fixed (duplicates)
- The today view and the kiosk no longer show a week-plan homework/remember item both as practical info and as a
  task: an item that produced tasks is shown only under "Husk og lektier".
- The kiosk showed a substitute twice on one line (text plus a "vikar" tag).

### Removed
- HelloFresh leftovers in the frontend (recipe dialog and meal-card CSS) and dead feed code for messages/private
  threads (messages are not shown in the feed). Duplicate constants (`MONTH_NAMES`, `pad2`).

### Changed (weather source)
- Weather now comes from MET Norway (yr.no, Locationforecast 2.0) instead of DMI, which kept answering
  400/429. No key; the User-Agent carries a contact (default: the repo link, `[weather] contact` overrides).
  Follows MET's terms: 2 decimals, never before `Expires`, `If-Modified-Since`/304. Hour icons use MET's own
  weather symbols (incl. night, fog and thunder). `family.json` keeps the same shape (`kilde`: "MET Norway");
  the briefing footer credits MET Norway. An old DMI cache is not reused.

### Added (weather hour by hour)
- The date heading shows today's weather as one emoji with min–max temperature (tomorrow after 19:00); tap it to
  fold out the rest of the day hour by hour (icon, temperature, rain). The kiosk weather line folds out the same way and closes itself after 30 seconds.
- `family.json` → `weather.dage[].timer`: hours 06–22 for display only (never sent to the AI). Night hours get a
  moon instead of a sun.
- The server log writes one weather line per run – how many days were fetched, or why there is no weather
  (home not set, DMI unavailable, turned off). Never coordinates.
- `/api/status` reports `weather: {enabled, home}`; the "Vejr: hjem" button turns red ("Vejr: hjem er ikke sat")
  when weather is on but the home location is missing.

### Fixed
- DMI rejected every weather request with HTTP 400: the cloud-cover parameter is called `fraction-of-cloud-cover`
  in DMI's EDR API, not `cloudcover`. A rejection now logs DMI's own reason (with decimals redacted, so never
  coordinates), and a test pins the parameter names.

### Changed
- Weather emoji carry U+FE0F so Windows and Android draw them in colour instead of as black text glyphs.

### Added (AI calendar items, phase 3)
- The AI reads new Aula messages (never private), feed posts and week plans and prefills "Føj til kalender" with
  title, date, time, place and child. The button only appears on items where something was found. Cancellations,
  postponements and moves of events the app created are shown on the item. Nothing is written without a click.
- Every suggestion is checked against the item's own text (date, time, place; changes must match exactly one
  app-created event). Each item is checked once; first run covers the last 14 days; at most 3 requests per fetch;
  30 requests a day are kept for the overview. Without AI, the rules decide, item by item.
  `[calendar_ai] enabled` in config.toml.

### Changed (AI narrative)
- With AI, the day and week overviews are a warm, chronological narrative in a few short paragraphs (~150 words),
  addressed to the parents, shown in the app and on the kiosk ("Dagen"). The normal school timetable is left out;
  only deviations (substitute, notes) are mentioned.
- The AI now receives all calendar events (incl. place and note), school news and non-private messages.
  Private threads are still never sent; phone numbers, mail addresses and CPR numbers are still removed.
- A narrative is rejected (fallback used) if its sources don't exist, it mentions a clock time not in the data,
  its length is off, or it contains formatting. Without AI, the rule-based list is unchanged.

### Added (weather)
- Weather from DMI (HARMONIE, no key) in the day and week overviews (section "Vejr": short forecast and practical
  advice, also without AI) and a weather line on the kiosk. The week only has weather for the days DMI covers.
- "Vejr: hjem" in the app: set the home location once from the browser on the server PC. Rounded to ~1 km and
  stored in `web/home_location.json`; only that is sent to DMI, at most hourly.
- Selftest lines for the home location and DMI. `[weather] enabled` in config.toml.

### Changed (Windows)
- The app reads `.env` itself when run directly (variables set with `setx` or by Docker still win). Selftest hints
  say where a secret can go. README: Windows steps (virtual environment, `Activate.ps1`, `setx` instead of `$env:`).

### Added (AI, phase 1)
- `ai.py`: one interface for language models. Google Gemini (free tier, `gemini-3.5-flash-lite`) is the default;
  Claude works through the same path. Plain HTTPS with httpx, JSON output, cache by content hash
  (`web/ai_cache.json`), daily budget and per-minute limit (`web/ai_usage.json`, day counted in Pacific time),
  and a growing pause after 429/402/401/403/5xx/timeouts/invalid output.
- `[assistant] mode = "ai"` and a new `[ai]` section (`provider`, `model`, `api_key_env`, `daily_cap`, `rpm`).
  `GEMINI_API_KEY` in `.env`.
- The day and week overviews are written by the AI. If it is unavailable, the last AI overview for the same
  day/week is kept and marked as possibly out of date; otherwise the app's own rules make it.
- Banner "AI ikke tilgængelig" at the top of the app and the kiosk when the shown overview did not come from the AI.
- The selftest sends one small request without family data and explains failures (missing/rejected key,
  payment required, quota). `/api/status` (login only) shows AI provider, usage and last error – never the key.

### Changed (AI, phase 1)
- CPR numbers are now removed from everything sent to a language model (only phone numbers and mail addresses were).
- AI answers are validated: unknown section titles make the answer invalid, and points without a valid source are dropped.
- `mode = "claude"` still works, but now goes through `ai.py`.

### Removed (AI, phase 1)
- The `anthropic` Python package. Claude is called over plain HTTPS instead.

### Removed (second round)
- Automatic calendar suggestions: the Forslag tab, its badge, the "nye forslag" banner and the ntfy push
  (`notify_suggestions`). New events are only created manually via "Føj til kalender".
- Learned rules (`/api/learned*`, `learned_rules.json`, "Foreslå lignende aktiviteter fremover"). They only existed to
  produce suggestions. An existing `learned_rules.json` is ignored and can be deleted.
- The "I dag / I morgen" toggle. The overview still switches to tomorrow automatically after `evening_hour`.

### Added (second round)
- Cancellations and moves of calendar events are shown on the message, post or weekly-plan item they come from
  ("Ændring i kalenderen"), with Fjern fra kalender / Flyt aftalen / Behold.
- Search in the feed (posts and albums): title, text and author, all words must match, hits highlighted, respects the
  person filter and type tabs, `Esc` clears.

### Changed
- "Add to calendar" now only creates events directly in the family calendar via the service account. The Google
  Calendar link fallback is removed: without direct writing the button is disabled and the reason is shown, and
  nothing is marked as created just because Google Calendar was opened.
- The calendar the app writes to is chosen explicitly: `[calendar_write] calendar_id`, else the `[[google]]` entry
  with `write = true`, else the only `[[google]]` entry. With several calendars and no choice, the app does not guess.
- When the service account works, the write calendar is read via the Google Calendar API instead of iCal
  (`read_via_api`, default on), with the iCal URL as fallback. App-created events are marked `appCreated`.
- Times: every view shows start–end when the end is known, and only the start ("sluttid ukendt") when it is not.
  Events without an end are marked `endInferred` instead of silently getting one hour. iCal `DURATION` is read.
- Lessons without an end last `[aula] lesson_minutes` (default 45) instead of running to the next lesson's start;
  suggested tests and activities "in a lesson" with only a start time also get 45 minutes.

### Removed
- `[calendar_write] reminder_minutes`. Per Google's API, event reminders apply only to the authenticated user (the
  service account), so they never reached the family. The selftest flags the old setting; use each parent's default
  notifications for the family calendar instead. (Not yet verified against the real Google service.)

### Added
- The event dialog shows which calendar the event goes to and who it is assigned to, based on names in the title.
- "Fortryd" (undo) in the confirmation right after creating an event.
- Selftest: checks reading via the API, warns when the write calendar is not one the app shows.
- Browser tests for time ranges in all views, the target preview and undo.
- `CLAUDE.md` with architecture, conventions, privacy rules and current status.
- GitHub Actions CI: full test suite (including browser and accessibility tests), Docker build with smoke test,
  and a gitleaks secret scan.
- Pre-commit hooks: gitleaks, private-key detection, large-file check, and a guard that blocks config, tokens and
  family data from being committed.

### Fixed
- Accessibility: past lessons and the blue accent now meet WCAG AA contrast in light and dark mode (`--accent`
  token), and the page has `<main>`/`<nav>` landmarks. `KNOWN` in `test_a11y.py` is empty.
- The school day in the timetable no longer ends at the last lesson's start when that lesson has no end time.
- `.gitignore` and `.dockerignore` now cover server state files written next to `family.json`
  (`server_state.json`, `suggestions_state.json`, `learned_rules.json`).
- README test instructions now install `requirements.txt` as well; `requirements-dev.txt` alone is not enough.
- Browser tests fail instead of silently skipping when `FAMILIEPLAN_REQUIRE_BROWSER` is set (used in CI).

## [0.7.0] – 2026-10-01

First version in git. Imported unchanged from `familieplanner (7).zip`.
