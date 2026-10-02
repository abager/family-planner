# Changelog

All notable changes to this project. Versions are git tags. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

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
