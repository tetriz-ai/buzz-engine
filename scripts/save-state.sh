#!/usr/bin/env bash
# Non-LLM writer: commit only the fixed state data file, never generated code.
set -euo pipefail
python3 - <<'PY'
import json
from pathlib import Path
path = Path('buzz/state/seen.json')
if path.is_symlink() or not path.is_file():
    raise SystemExit('Expected a regular buzz/state/seen.json file')
if path.stat().st_size > 10 * 1024 * 1024:
    raise SystemExit('State file exceeds 10 MiB')
state = json.loads(path.read_text())
if not isinstance(state, list) or any(not isinstance(item, str) for item in state):
    raise SystemExit('State must be a JSON array of string IDs')
PY
if git diff --quiet -- buzz/state/seen.json && git ls-files --error-unmatch buzz/state/seen.json >/dev/null 2>&1; then
  echo 'No state changes'
  exit 0
fi
: "${GH_TOKEN:?GH_TOKEN is required only in the non-LLM state-save step}"
branch="buzz/state-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git switch -c "$branch"
git add -- buzz/state/seen.json
git -c core.hooksPath=/dev/null commit -m 'chore: update buzz seen state' --only -- buzz/state/seen.json
# Authentication is process-scoped; no credential is persisted in git config.
auth_header="$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')"
git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $auth_header" push origin "$branch"
gh pr create --title 'chore: update buzz seen state' --body 'Updates only the buzz seen-log from the scheduled scan.' --head "$branch"
# Leave the PR for review; repository protection and approval policies still apply.
