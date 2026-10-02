# Changelog

All notable changes to this project. Versions are git tags. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

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
