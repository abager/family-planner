# Changelog

All notable changes to this project. Versions are git tags. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Changed (progress)
- The progress bars moved out of the status line: a small ring left of the gear now shows the overall progress
  (weighted with each part's own percentage), and tapping it opens the per-type bars. Nothing else on the page
  moves when a fetch starts or ends. Red if a part failed, ✓ for a few seconds afterwards, then hidden.

### Added (progress bars)
- While the server fetches, the status line shows one compact progress bar per data type instead of the spinning
  icon: a real percentage where the amount is known (Google calendars, message threads, albums, overview), a busy
  animation otherwise. A failed part turns red until the fetch ends; afterwards the bars show "færdig" for a few
  seconds and disappear. Not on the kiosk. `/api/status` has a new `progress` field (after login only).

### Changed (Aula fetch)
- Faster, gentler Aula fetch: the parts run concurrently behind one shared cap of 3 simultaneous calls, each call
  has a timeout, and temporary errors are retried a few times (never login errors). Log query strings are removed.
- Messages are fetched incrementally: unchanged threads are reused without calls. Every hour a deep check fetches
  every thread again, so edited and deleted messages show within an hour (`deep_check_minutes`).
- A part of the Aula fetch that fails (e.g. albums) keeps its previous data and is listed under ⚠ until it works.
- New `[aula]` settings in `config.example.toml`: `max_concurrent`, `request_timeout`, `max_retries`, `part_timeout`,
  `messages_stop_after_unchanged`, `messages_max_pages`, `deep_check_minutes`, `lesson_notes_max`.

### Added (errors at the title)
- ⚠ with a count next to "Familieplan" whenever AI, Aula, Google Calendar (read or write), weather or ntfy fails.
  Tapping it opens a dialog with the full error, when it happened and how often, what to do, and a link where
  there is one (e.g. "Log ind" for Aula). An error disappears the next time that integration succeeds, or on
  restart. Not shown on the kiosk. Error texts are scrubbed of keys, tokens and coordinates and never contain
  family data.

### Changed
- The yellow "Familieassistenten er ikke tilgængelig" banner at the top is replaced by a subtle line just below the
  overview's text, one per overview ("I dag" and "Ugen"), on the kiosk too. Same wording as before.
- The red warnings in the status line are gone; "Data er over en time gamle" and "Hjem for vejret er ikke sat"
  are listed in the new dialog instead. The AI banner above the overview is unchanged.
- The AI hints shown by the self-test now live in `ai.py` (`ai.HINTS`) and are shared with the dialog.

### Fixed (AI overview)
- An invalid answer from the language model (not valid JSON) paused all AI for hours and grew with every retry,
  because the same data gave the same bad answer. Now Gemini is asked to follow a fixed answer format
  (`responseSchema`) for the overview and the calendar suggestions, and the prompt's format example is valid JSON.
- An invalid answer is retried once at once. If that also fails, only that content waits (15 min, doubling to 6 h);
  new data and other AI features carry on. An old pause of this kind is ignored after the update.
- The log says why AI was not used: the fallback line names the reason and, during a pause, the last error. Reasons
  that send nothing (missing key, pause, budget) are logged once instead of not at all.

- A correct overview was rejected when the data gave a time as a range ("kl. 8-13"): the end time 13.00 did not
  count as being in the data. The time check now also reads range ends tied to a time ("kl. 8-13", "8.00–13",
  "fra kl. 8 til 13") and "klokken 13". Bare number pairs ("side 12-20") still do not count.
- The log separates an answer that is not valid JSON ("svaret var ikke gyldigt JSON …") from valid JSON that fails
  the app's check ("svaret holdt ikke appens tjek …").
- The overview prompt asks for correct, natural Danish (no Norwegian or Swedish words, everyday words, short
  sentences); calendar suggestion titles likewise.

### Added
- The last invalid answer is saved in `secrets/ai_last_invalid.json` (owner-only, never logged, never served, never
  in git) so you can see what the model wrote.

### Changed (desktop, tablet and phone)
- The whole app now has the kiosk's look: the background is the sky of the focus day's weather (sun, partly cloudy,
  overcast, rain, frost; sage without weather), and 21–06 everything is dark. After 18:00 it is tomorrow's sky, like
  the rest of "I dag". The device's light/dark setting is no longer used, and a week without forecast keeps today's
  sky. The status bar colour follows the top of the screen everywhere. The system font replaces the two Google fonts
  (nothing to load, works offline); headings are lighter. Dialogs and menus keep a solid background over the sky.
- "I dag" is a shared time axis with one lane per person (children first, then adults): lessons and institution
  faint, events in the person's colour, family events as one block across all lanes (one per lane when something
  overlaps them), overlapping things for one person side by side. The axis is 07–21 and stretches when something lies
  outside it; hourly weather sits at the hours; a red line shows the time (today only); finished things are dimmed and
  the current lesson is marked. All-day events and the week plan's practical info are in a row at the top. Tapping a
  block opens its details. Under 700 px the day is one list in time order (an event for several people once, with
  their icons; school as one line per child). Replaces "Skema", "Dagens aftaler" and "Praktisk info".
- The overview in "I dag" is at most four lines; "Vis hele overblikket" unfolds it. The overview in "I dag" and
  "Ugen" uses the panel's full width (it was capped at 68 characters, so text was cut although there was room).
- "Husk og lektier" in "I dag" shows the focus day and the next two days, each on its own (same rule as the kiosk's
  "Husk"), instead of the focus day only.
- "Ugen": the day's weather in each day header, past days dimmed, a person with nothing all week is one line.
- Feed and Beskeder: only the new colours, surfaces and font.

### Fixed
- Week board: a pill's title could run out of the pill (no break between the time and the title).

### Changed (kiosk)
- New kiosk layout: one dark screen for an iPad in landscape. Top bar with clock, day, status and weather (hour now,
  min–max, advice, a strip of the next hours); the overview in full width (max 4 lines, cut at a sentence with "…");
  one column per person – children first, then adults – with lessons and events in time order and family events in
  every column; a narrow "Husk" column for the focus day and the next two days. Replaces the per-child cards, the
  side cards ("Overblik"/"Husk og lektier" and "Dagens aftaler") and the fold-out weather hours.
- Tapping a lesson, event, task, the weather or the overview opens details that close after 10 seconds or on a tap
  outside. Columns that don't fit end with "+N flere" (the font shrinks at most 2 px first).
- Kiosk look: light by day with a background that is the sky of the day's weather (sun, partly cloudy, overcast,
  rain, frost; sage without weather), dark 21–06 so it doesn't glare. Smaller, lighter type in the iPad's system
  font; thin rules instead of filled cards. Text keeps WCAG AA contrast on every sky. The rest of the app is unchanged.

### Fixed (kiosk)
- The iPad's status bar stayed the app's dark colour above the light kiosk: the kiosk now sets `theme-color` to the
  top of its sky (dark at night) and restores the app's own values when it closes.
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
