---
name: buzz-engine
description: Set up Buzz, scan Product Hunt, Hacker News or Reddit, prepare research notes or copy guidance, generate a daily digest, or report brand mentions. Use for Buzz setup and engagement workflows; platform posting remains manual.
---

# Buzz for Codex

Use the existing command procedures in this repository. Resolve `engine_root` to the
real directory containing this `SKILL.md`, following the installed skill symlink.
Keep the current working directory at the user's project repository, not this engine
checkout: config, working files, state and learnings belong to the user's project.

| Request | Read and follow |
| --- | --- |
| Setup or `init` | [commands/buzz-init.md](commands/buzz-init.md) |
| `ph`, `hn`, `reddit`, or `brands` | [commands/buzz.md](commands/buzz.md), with `--mode` set to the requested lane |
| Daily run or `daily` | [commands/buzz-daily.md](commands/buzz-daily.md) |

If no action is supplied, ask whether the user wants setup, a lane, the brand report,
or a daily digest. Preserve the user's arguments, including `--dry-run` and `--pdf`.
Read the command, requested lane's rubric, voice and project learnings before running
the corresponding phases. Do not maintain a second copy of those procedures here.

HN output is research only: never generate, edit, or post an HN comment. Other copy
recommendations are guidance under platform rules; readers write their own content.
Filtered signals are for independent reading, never coordinated voting.

## Host mapping

- `${CLAUDE_PLUGIN_ROOT}` in the shared procedures means the resolved `engine_root`.
  Replace it with the quoted absolute engine path in each shell command. It is not an
  environment variable supplied by Codex. `$ARGUMENTS` means the user's request arguments.
- `/buzz`, `/buzz-init` and `/buzz-daily` mean follow the corresponding file above in
  this session. Daily lane calls reuse `commands/buzz.md`; do not launch Claude.
- Where a command delegates to `buzz-engine:humanizer` through Claude's Agent tool,
  spawn a Codex subagent. Supply [agents/humanizer.md](agents/humanizer.md) as its
  instructions, the lane rubric, current voice, learnings, the command's dispatch block,
  and the grounded thread evidence. Explain the engine path mapping to that subagent.
  Use the agent's Markdown body as its instructions; Claude-specific frontmatter such
  as `model: opus` and `tools: Read, Grep, Glob` does not configure Codex. Inherit the
  current Codex model and map the requested reads to its available tools.
  Wait for its result and apply the existing anti-AI pass. Do not silently replace a
  required humanizer dispatch with the orchestrator drafting the comment. If subagents
  are unavailable, report the blocker and leave the affected drafts incomplete.
- Claude marketplace commands and the Actions runner are Claude-specific.
  Codex uses this skill and the same Python scripts. The upstream GitHub Actions
  template remains a Claude runner; do not claim it schedules Codex. A Codex scheduler
  needs a separately configured runner and the same project config and credentials.

Honor the project's permissions. A request to scan or draft does not by itself authorize
Slack posts, a recurring schedule, commits, pushes, or PR merges. Use `--dry-run` for
an initial scan unless the user explicitly authorizes a live digest. Keep state writes
and Git actions within the user's approved scope. Tokens remain in the environment or
the project's ignored `buzz/.env`; never inspect or echo them to infer availability.

Product Hunt, HN, Reddit and LinkedIn actions remain manual. The only permitted delivery
channel is the configured Slack digest when authorized. Follow the upstream dry-run,
dedupe, proof, humanizer and platform rules without weakening them for this host.
