# CLAUDE.md

## What this is

buzz-engine is a Claude Code plugin with three commands: `/buzz` scans one platform and provides research notes or copy guidance, `/buzz-daily` runs every platform on a schedule, and `/buzz-init` writes a user's config.
It reads Product Hunt, Hacker News and Reddit, and writes local reports or sends a Slack digest. It never acts on any platform.

## Layout

- `SKILL.md`: native Codex adapter that reads the same commands and maps host paths and subagent dispatch. `scripts/install-codex.py` links it into a project without copying source.
- `commands/`: the three commands. `buzz.md` is the pipeline: scan, judge, draft, digest, then saving the seen-log.
- `agents/humanizer.md`: prepares copy guidance for Product Hunt and Reddit; HN cards remain research only. It reads the user's `buzz/voice.md`, or `templates/voice.md` as the default.
- `rubrics/`: what a good comment is on each platform. `/buzz` reads the lane's rubric plus the user's `buzz/learnings/<lane>.md`.
- `scripts/`: `buzz-scan.py` (HN and Reddit), `buzz-ph-scan.py`, `buzz-digest-pdf.py`, `buzz-slack-upload.py`, `buzzpaths.py` (where the user's files are) and `vendor.py` (copies the plugin into a repo for interactive hosts without plugin loading).
- `templates/`: the starter config, the default voice, the Slack app manifest and the GitHub Action.
- `tests/`: offline tests. No network.
- `docs/`: platform limits, why each config default is what it is, and the changelog.

## Commands

Python 3.9 is the floor. CI tests 3.9 and 3.12. These are CI's steps:

```sh
pip install -r requirements-dev.txt
ruff check --select E4,E7,E9,F scripts tests
python3 -m unittest discover -s tests -v
claude plugin validate .
```

To run the plugin from this checkout against a scratch repo, without posting:

```sh
claude -p "/buzz --mode hn --dry-run --pdf" --plugin-dir /path/to/buzz-engine
```

## Contract to keep

- Nothing posts, comments, upvotes, replies or follows on Product Hunt, Hacker News or Reddit. Slack delivery runs through `buzz-slack-upload.py`; the separate trusted state job can push the seen-log and open a GitHub PR. Source-reading commands never perform Git writes.
- Use neutral examples and no private operational history or personal account identities. Install commands use `tetriz-ai/buzz-engine`.
- Scripts find the user's files through `buzzpaths.py`, which resolves against the git root of the current directory, never against the plugin's own path. Pinned by `TestPaths`.
- `--dry-run` posts nothing and never writes `buzz/state/seen.json`. Scanners get `--peek` in a dry run.
- A missing token is reported only by the script that needs it. Commands never read `buzz/.env` to decide.
- Every command and agent opens with YAML frontmatter on line 1. Pinned by `TestFrontmatter`.
- `vendor.py` output contains no `${CLAUDE_PLUGIN_ROOT}` and no `buzz-engine:` prefix, and never overwrites a file it didn't write. Pinned by `TestVendor`.
- The PDF renderer makes no network calls, and only reads the config when PyYAML is present. `TestDigest` covers the render.

## Making a change

- Write the test first. A behaviour change needs a case that fails before the change and passes after it.
- Plugin files are referred to as `${CLAUDE_PLUGIN_ROOT}/...` and the user's files as `buzz/...`.
- A renamed config key, a removed flag or a change to what gets posted needs a `docs/CHANGELOG.md` line under Unreleased. The new key also goes in `templates/buzz.config.yaml`.
- Check every flag against the script's argparse before documenting it.
- Commits use Conventional Commits. A maintainer reviews pull requests.

## Do not

- Do not add any call that writes to Product Hunt, Hacker News, Reddit or LinkedIn.
- Do not print, log or commit a token, and do not read `buzz/.env` from a command.
- Do not use the Slack connector for posting. The bot token is the only path, because the connector can't upload the PDF.
- Do not put a comment above a command's frontmatter. It must be line 1.
- Do not make the PDF renderer need PyYAML. It has to run where only the scanner output exists.
