# Setup

From nothing to a daily digest in your Slack channel. About 20 minutes.

Each code block is labelled with where it runs:

- **Terminal:** your shell.
- **Claude Code:** typed into a `claude` session.

Run everything from your repo's folder.

---

## Before you start

Have these ready. Most of the 20 minutes is waiting on one of them.

- [ ] **A repo for your team's config.** Any repo works, including a new empty one. It
  holds `buzz/config.yaml` and the seen-log. `cd` into it for every step below.
- [ ] **A Slack channel** for the digest, and permission to add an app to your
  workspace (or someone who has it).
- [ ] **Who reviews.** Choose who reads the guidance and verifies facts and platform rules.
  HN output is research only; contributions must be written independently.
- [ ] **On your machine:** Claude Code, Python 3.9+ with the pinned requirements (`pip install -r /path/to/buzz-engine/requirements.txt`),
  and Chrome for the PDF.

---

## 1. Choose a run mode

Use the [GitHub Actions workflow](#6-schedule-it) for unattended runs. It separates the
source-reading model from Slack delivery and GitHub state writes. Use an interactive
local session for previews or explicitly requested live runs.

The former claude.ai routine setup is retired. Do not migrate its Slack or GitHub write
credentials into another model runner. Disable any existing Buzz routine before moving
the schedule to Actions, and remove its delivery/write credentials from that environment.

---

## 2. Get the tokens

### Slack bot token (required)

First, check whether your workspace already has the bot: in Slack, open Apps and search
for "Buzz Digest". If it's there, ask a workspace admin who installed it and get the token
from that person. Then skip to item 5 below, since one bot can post to any number of
channels.

Otherwise, create it:

1. Open https://api.slack.com/apps, then Create New App, then From a manifest.
2. Pick your workspace and paste the contents of
   [templates/slack-app-manifest.yaml](templates/slack-app-manifest.yaml).
3. Click Install to Workspace. If your workspace needs admin approval, the button
   says Request to Install instead, and you'll have to wait for an admin.
4. Open OAuth & Permissions and copy the Bot User OAuth Token. It starts with
   `xoxb-`. This is your `SLACK_BOT_TOKEN`.
5. In your digest channel, type `/invite @buzz-digest`. **This is the step people
   forget.** Without it, every post fails with `not_in_channel`.
6. Copy the channel ID: click the channel name, and it's at the bottom of the window. It
   starts with `C`.

Why a bot at all: the digest is a PDF, and Claude's Slack connector can post text but
can't attach files. More in [Notes](#notes).

### Reddit identification

Set `reddit.user_agent` in `buzz/config.yaml` to your own descriptive client identity,
including your own contact information. Do not use a browser identity or another user's
handle. Check Reddit's current access terms; the scanner stops on blocked requests.

### Product Hunt token (optional)

The [PH API documentation](https://api.producthunt.com/v2/docs) restricts commercial use.
Contact Product Hunt for permission before business use. A developer token alone does
not establish that permission.


1. Open https://www.producthunt.com/v2/oauth/applications and add an application. Any
   name works, and the redirect URI can be `https://localhost`.
2. Copy the Developer Token. This is your `PH_API_TOKEN`.

Without it, only the Product Hunt lane is skipped. Hacker News and Reddit need no
tokens.

---

## 3. Add buzz-engine to your repo

For Codex, use 3C. For Claude, use 3A; 3B is an optional export for interactive
hosts without plugin support. Use only one installation method in a project.

### 3A. Install the plugin

Terminal:

```bash
claude plugin marketplace add tetriz-ai/buzz-engine --scope project
```

```bash
claude plugin install buzz-engine@buzz-engine --scope project
```

This records the plugin in `.claude/settings.json`, so teammates who clone the repo get
it too.

### 3B. Export commands for an interactive host

For a host that cannot load plugins, export the commands into your project. This
installs files only; it does not configure a scheduler or isolate credentials.

Terminal:

```bash
git clone https://github.com/tetriz-ai/buzz-engine ~/buzz-engine
```

```bash
python3 ~/buzz-engine/scripts/vendor.py --into .
```

The vendored `buzz/engine/` includes LICENSE and NOTICE. Keep both when redistributing.
Updates preserve edited and unowned files unless you pass `--force`. Content hashes in
`buzz/engine/.vendor-manifest.json` track installed files; commit that file too. Legacy
files without a manifest are accepted only when they match the current output exactly;
review other legacy files before using `--force`. Symlink destinations are refused even with `--force`.

This writes `.claude/commands/buzz*.md`, `.claude/agents/buzz-humanizer.md` and
`buzz/engine/`. Review and commit those files yourself if they belong in your repo.
To update later, update your Buzz checkout and rerun the exporter.

---

### 3C. Link the Codex skill

Clone Buzz to a persistent checkout and run:

```bash
python3 /path/to/buzz-engine/scripts/install-codex.py --into . --dry-run
python3 /path/to/buzz-engine/scripts/install-codex.py --into .
```

This links `.agents/skills/buzz-engine` to the checkout without copying its commands or
scripts. Each machine needs that checkout and link. In Codex, run `$buzz-engine init`,
then `$buzz-engine hn --dry-run --pdf`. Use `daily` for the shared daily procedure and
`ph`, `reddit`, or `brands` for those modes. The skill maps paths and humanizer delegation
to Codex while reading the existing command files. It never launches Claude.

The following scheduler examples are Claude-specific. Do not use the Claude GitHub Action
as a Codex runner. Verify a separate Codex scheduler before enabling recurring work.

---

## 4. Configure

Claude Code:

```
/buzz-init
```

It asks for:

| Question | Example answer |
|---|---|
| Product name, plus old names and the company name | `Acme, AcmeHQ, Acme Labs` |
| Your domain | `acme.dev` |
| What counts as "your space" | `developer productivity; AI coding tools; code review` |
| Slack channel ID | `C0123456789` (from step 2) |
| Timezone | `Asia/Kolkata`. It decides what counts as a weekday and dates the digests. The run time itself is set in step 6 |

It writes `buzz/config.yaml` and an empty `buzz/.env`, which is never committed, and
finishes with a short test scan. Missing tokens show up as warnings, not errors.

Then:

1. **Put your tokens in `buzz/.env`,** whichever option you picked. The test in step 5
   needs them there. The file is gitignored.
   ```
   SLACK_BOT_TOKEN=xoxb-...
   PH_API_TOKEN=...
   ```
2. **Fill in the gaps.** Run `grep -n FILL_IN buzz/config.yaml` in a terminal. What's
   left is usually just the posters' usernames, under `accounts`.
3. **Check the subreddits and keywords** in `buzz/config.yaml`. The defaults suit a
   developer-tools product, so change them if yours isn't one.
4. **Commit and push** `buzz/config.yaml`, `buzz/learnings/` and `.gitignore`.

---

## 5. Test it

Claude Code (safe to repeat, posts nothing):

```
/buzz --mode hn --dry-run --pdf
```

Open the PDF in `buzz/working/` and read the HN research notes. If the threads feel off-topic,
change the "your space" list (`product.relevance_pillars`) and `keywords` in the config.

Then send one real post:

```
/buzz --mode hn --pdf
```

One message with the PDF should appear in your channel. If it doesn't, see
[Troubleshooting](#troubleshooting).

---

## 6. Schedule it

GitHub Actions is the supported unattended runner. Its model step runs
`/buzz-daily --no-post`, with separate non-model delivery and state jobs. By default it
runs weekdays, scanning Product Hunt, Hacker News, then Reddit.

Copy [templates/github-action.yml](templates/github-action.yml) to
`.github/workflows/buzz-daily.yml` in your repo. Review its pinned engine revision and
restricted tool permissions before enabling the schedule. Set the model credential,
`SLACK_BOT_TOKEN`, and optional `PH_API_TOKEN` as Actions secrets.

Use `state_dir: buzz/state` and `state.commit: none` in `buzz/config.yaml`. The scan job
has read-only GitHub permissions, no Slack credential, and no GitHub write token
available to the model. It uses `--no-post` to write PDFs locally; a separate trusted
job validates fixed-name PDF artifacts and sends them to the configured Slack channel.
A separate job validates the JSON seen-log and opens a PR changing only
`buzz/state/seen.json`, using a write token outside the model process. Merge that PR
after review so the next fresh runner retains deduplication. The fixed path prevents source text from
choosing what to commit. Review the state job's changes before relying on its output.

The scanner uses scoped script commands; arbitrary Bash and WebFetch are not allowed.
If required context is unavailable through permitted tools, the digest reports that gap.
Open Actions, choose buzz-daily, and run it once to verify your configured environment.

### Interactive previews on your laptop

Use the GitHub Actions template for unattended runs. Its job boundary keeps GitHub
write credentials out of the model process. A personal cron command inherits local
credentials and settings, so the unrestricted headless example is intentionally removed.
For an interactive preview run `/buzz --mode hn --dry-run --pdf` from a scratch project.

---

In the Actions workflow, the model receives its own model credentials and the optional read-only PH
token. Slack and GitHub write credentials are restricted to separate jobs. Tool
permissions and data-handling instructions reduce exposure; they are not an OS sandbox.
Do not run unattended against a repository or sources you do not trust to that extent.

## Every day after that

1. Open the digest in Slack.
2. For each card, read the thread and verify its facts. Treat recommended copy as
   guidance and follow platform rules. Write HN content independently; never post
   generated or AI-edited HN text. Disclose relevant affiliations.
3. Use filtered signals to focus your reading; decide independently whether to participate.
4. Every week or so, note in `buzz/learnings/<lane>.md` which comments got replies and
   which were ignored. The next drafts take that into account.

---

## Troubleshooting

| What you see | Fix |
|---|---|
| `not_in_channel` | Type `/invite @buzz-digest` in the channel |
| `invalid_auth` or `Missing SLACK_BOT_TOKEN` | The token isn't where this run looks: `buzz/.env` for an interactive local run, or the delivery job's repo secret on Actions |
| `/buzz` appears twice | Both 3A and 3B were done in one repo. Undo one |
| Yesterday's threads show up again | On Actions, review and merge the delivered-state PR. For local runs, retain the existing `buzz/state/` files |
| `reddit feeds: 3/12 ok ... throttled` | Normal. Reddit limits requests per IP address, and the digest lists the missing subreddits |
| `⚠ thin day` for Product Hunt | The day's launches had barely started. Normal just after midnight Pacific |
| Product Hunt `429` | Too many requests in 15 minutes. Wait, or cut `ph.topics` in the config |
| An HTML file instead of a PDF | Chrome isn't installed. Install it, or install the pinned requirements, then run `python -m playwright install chromium` |
| A laptop run ends with `Reached max turns` | A personal hook is stalling it. Review your local hooks or pass `--setting-sources project,local` |

More on platform limits: [docs/platform-notes.md](docs/platform-notes.md).

---

## Notes

- **Why a Slack bot:** The digest's main output is a PDF, and Claude's Slack connector can
  post text but can't attach files. Slack's file upload needs a bot token, and incoming
  webhooks can't upload files either. A bot token also works the same on every runner.

- **What the bot can do:** It can post a message and upload a file to channels it's been
  invited to (`chat:write`, `files:write`). It can't read messages or reactions, and it
  can't touch Product Hunt, HN or Reddit. The "👍 approve" line on cards is a convention for
  people. Nothing reads it back.

- **Keeping the token safe:** On Actions, give the Slack token only to the delivery job.
  Remove it from any retired Buzz routine. To revoke a leaked token, open the Slack
  app's OAuth & Permissions, revoke its tokens, reinstall, and update the delivery secret.

- **The schedule and daylight saving:** The Actions cron schedule uses UTC. `5 8 * * 1-5`
  lands 5 minutes after Product Hunt's daily reset in winter (PST) and about an hour after
  it in summer (PDT). The Product Hunt lane goes first because makers watch their launch
  page most closely in that first hour.

- **Community restrictions:** Check current subreddit rules before participating. Research
  signals do not grant permission to post; do not warm accounts or bypass restrictions.
