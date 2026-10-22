# Contributing

Thank you for helping. Please open an issue before starting on a new lane, a new command
or a change to what gets posted, so we can agree on it before you write the code.

## Run the checks

These are the same checks CI runs:

```sh
pip install -r requirements-dev.txt
ruff check --select E4,E7,E9,F scripts tests
python3 -m unittest discover -s tests -v
claude plugin validate .
```

To see a change end to end, run it against a scratch repo without posting anything:

```sh
claude -p "/buzz --mode hn --dry-run --pdf" --plugin-dir /path/to/buzz-engine
```

## Every change needs a test

A pull request without a test is not merged. Offline tests live in
`tests/` and never touch the network. User-visible changes need a `docs/CHANGELOG.md`
entry. Before 1.0, a renamed config key or a removed command flag counts as breaking
(see "Versioning and compatibility" in the README).

## Changing a command or rubric

- Use neutral examples without private operational details or personal account identities.
  Install commands use `tetriz-ai/buzz-engine`.
- Refer to plugin files as `${CLAUDE_PLUGIN_ROOT}/...` and to the user's files as
  `buzz/...`. `scripts/vendor.py` rewrites the first form for interactive hosts without plugin loading.
- Nothing may ever post to Product Hunt, Hacker News or Reddit. Every platform action
  stays with a person.

## Commit style

Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/): a short subject
such as `docs: clarify the seen-log`, a blank line, then a body that says why.

## Sign-off

No sign-off line and no separately signed agreement are needed. What you submit in a pull request is offered under
the same license as the rest of the project (the [LICENSE](LICENSE)).
