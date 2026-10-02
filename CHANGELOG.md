# Changelog

All notable changes to this project. Versions are git tags. The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- `CLAUDE.md` with architecture, conventions, privacy rules and current status.
- GitHub Actions CI: full test suite (including browser and accessibility tests), Docker build with smoke test,
  and a gitleaks secret scan.
- Pre-commit hooks: gitleaks, private-key detection, large-file check, and a guard that blocks config, tokens and
  family data from being committed.

### Fixed
- `.gitignore` and `.dockerignore` now cover server state files written next to `family.json`
  (`server_state.json`, `suggestions_state.json`, `learned_rules.json`).
- README test instructions now install `requirements.txt` as well; `requirements-dev.txt` alone is not enough.
- Browser tests fail instead of silently skipping when `FAMILIEPLAN_REQUIRE_BROWSER` is set (used in CI).

## [0.7.0] – 2026-10-01

First version in git. Imported unchanged from `familieplanner (7).zip`.
