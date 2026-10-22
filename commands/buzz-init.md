---
description: Set up buzz-engine in this repo. Writes buzz/config.yaml, gitignores secrets and scratch, and proves the setup with a dry scan.
argument-hint: "[--product NAME] [--domain DOMAIN] [--pillars \"a; b\"] [--channel CHANNEL_ID] [--timezone TZ] [--state none] [--force]"
---

# /buzz-init: set up buzz-engine in this repo

Run this once in the repo that should own the buzz config and state. It writes files
only inside `buzz/` and appends to `.gitignore`. It never posts anything.

Arguments: `$ARGUMENTS`

---

## Retired scheduling options

If arguments include `--routine`, or `--state` has any value other than `none`
(including `--state=pr` or `--state=push`), stop before writing any files.
Explain that claude.ai scheduling and model-owned Git persistence are retired. Use the
isolated GitHub Actions workflow with `state.commit: none`; it saves delivered state
in a separate non-model job. Do not copy credentials or configure an alternate timer.

## Step 1: Check what's already there

- Work from the repo root: `git rev-parse --show-toplevel`. If this isn't a git repo,
  say interactive local state works, but Actions state persistence requires a git repo.
- If `buzz/config.yaml` exists and `--force` is not in the arguments: **stop**. Say it
  already exists, show its `product.name` and `slack.channel_id`, and tell the user to
  edit it directly or re-run with `--force`. Never overwrite a config silently.

## Step 2: Collect the answers

Take each value from the arguments when given. For anything missing: if you can ask
the user (an interactive session), ask for it, several at once. If you can't (a
headless `-p` run), leave the template's `FILL_IN_*` placeholder and list it at the
end.

| Answer | Config key | Notes |
|---|---|---|
| Product name (`--product`) | `product.name`, `product.never_mention`, `brand_monitor.brands.ours` | Ask for old names and the company name too; they all go in `never_mention` |
| Domain(s) (`--domain`) | `product.own_domains` | |
| What "our space" is (`--pillars`, `;`-separated) | `product.relevance_pillars` | 2 to 5 short phrases. The Judge scores relevance against these |
| Slack channel ID (`--channel`) | `slack.channel_id` | Starts with C. In Slack: channel name, then About, then the ID at the bottom |
| Timezone (`--timezone`) | `schedule.timezone` | IANA name. Default `UTC` |
| State handling (`--state`) | `state.commit` | Only `none` is accepted. Actions persists delivered state outside the model |
| Handles | `accounts.{reddit,hn,ph}[0].handle` | Optional now. One real human account per platform |

Also, in an interactive session, ask whether the example subreddits and keywords fit.
They're tuned for a dev-tools product. Offer to replace them.

## Step 3: Write the files

1. `mkdir -p buzz/state buzz/working buzz/learnings`
2. Copy `${CLAUDE_PLUGIN_ROOT}/templates/buzz.config.yaml` to `buzz/config.yaml`, then
   fill in the answers from Step 2. Edit values only and keep the comments.
3. Create `buzz/learnings/ph.md`, `hn.md` and `reddit.md` if they're missing, each with:
   ```
   # <Lane> comment learnings
   Record what happened to comments you posted. /buzz reads this before drafting.

   | Date | Thread | What we tried | Outcome | Lesson |
   |---|---|---|---|---|
   ```
4. Append these lines to `.gitignore` if they aren't already there:
   ```
   buzz/.env
   buzz/working/
   ```
   Do **not** ignore `buzz/state/`. The seen-log is meant to be committed.
5. If `buzz/.env` doesn't exist, create it with empty keys and tell the user to fill it:
   ```
   PH_API_TOKEN=
   SLACK_BOT_TOKEN=
   ```
   Never write a real token into any file yourself, even if the user pastes one in chat.
   Tell them to paste it into `buzz/.env` themselves.

## Step 4: Prove it works

Run these, and report each one as pass, fail or skipped:

1. `python3 -c "import yaml"`. On fail: install the pinned requirements from your Buzz checkout.
2. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-scan.py --platform hn --peek`. No
   credentials needed. Pass means it prints a candidate count and writes
   `buzz/working/candidates-<date>.json`.
3. If `PH_API_TOKEN` is set (in `buzz/.env` or the environment):
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-ph-scan.py --peek`. Otherwise skip, and
   say PH mode needs a token from https://www.producthunt.com/v2/oauth/applications.
4. If `SLACK_BOT_TOKEN` is set:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-slack-upload.py --text "buzz-engine connected" --channel <channel_id> --dry-run`.
   This checks the token without posting. Otherwise skip, and say the bot needs
   `chat:write` and `files:write` and must be invited to the channel.
5. PDF rendering needs Chrome or Chromium, or Playwright. Check with
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-digest-pdf.py --help`, and note that
   `--pdf` needs a browser.

`--peek` never writes the seen-log, so none of this uses up tomorrow's candidates.

## Step 5: Tell them what's next

A short block:

- Files written.
- Any `FILL_IN_*` still left: run `grep -n FILL_IN buzz/config.yaml`.
- The next command: `/buzz --mode hn --dry-run` to see a full digest without posting.
- To schedule it, copy `${CLAUDE_PLUGIN_ROOT}/templates/github-action.yml` to
  `.github/workflows/buzz-daily.yml` and use `state.commit: none`. The workflow runs
  with `--no-post` and delivers PDFs and saves state in separate trusted jobs.
- Local initialization checks token identity and local inputs only; it does not prove
  Slack posting scopes or channel membership. Verify a live delivery separately.
- Commit `buzz/config.yaml`, `buzz/learnings/` and `.gitignore`. Never commit `buzz/.env`.
