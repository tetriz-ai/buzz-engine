#!/usr/bin/env python3
"""
buzz-ph-scan.py — /buzz Product Hunt mode, Stage 1 (fetch).

Additive sibling of buzz-scan.py: it does NOT touch the HN/Reddit pipeline.
Scans Product Hunt (GraphQL API v2, needs a token) for relevant AI / dev-tooling /
eng-productivity launches, pulls their makers and anonymous discussion context, dedups
against the shared git-tracked seen-log (config `state_dir`, default
buzz/state/seen.json; ids are `ph:`-prefixed so they never
collide with `hn:`/`reddit:` ids), and writes candidates to
buzz/working/ph-candidates-<date>.json for the Judge stage to score + draft.

Deterministic. No judgment, no drafting, no posting, no upvoting, no connecting.
Zero external deps except PyYAML.

Usage:
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-ph-scan.py                    # scan PH launches
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-ph-scan.py --no-dedup         # ignore seen-log (testing)
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-ph-scan.py --peek             # dry-run: honour seen-log, don't write it
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-ph-scan.py --full-lookback    # ignore the PH-day window (testing)

PH token (required): put in buzz/.env
(gitignored — never commit it):
  PH_API_TOKEN=<developer token from https://www.producthunt.com/v2/oauth/applications>
The env var name is configurable via config.yaml → ph.token_env.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import yaml

# Python 3.9 stdlib, but it needs a system tz database. The routine container may
# ship without one, so the PH-day window degrades to the lookback window rather
# than crashing the daily run — see ph_day_start().
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — 3.8 and older
    ZoneInfo = None

from buzzpaths import REPO, CONFIG_PATH, ENV_PATH, require_config
PH_API = "https://api.producthunt.com/v2/api/graphql"
USER_AGENT = "buzz-engine/0.1"
# Product Hunt withholds voter/commenter identity behind this literal sentinel.
REDACTED = "[REDACTED]"



# Light list query per topic; discussion is fetched per-post afterwards
# (only for posts that survive the local filter) to stay inside PH's
# complexity-based rate limit.
LIST_QUERY = """
query($topic: String!, $postedAfter: DateTime!, $first: Int!, $after: String) {
  posts(order: NEWEST, topic: $topic, postedAfter: $postedAfter, first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      id name tagline description url website votesCount commentsCount createdAt
      topics(first: 6) { edges { node { name slug } } }
      makers { id name username headline url twitterUsername websiteUrl }
    } }
  }
}
"""

DETAIL_QUERY = """
query($id: ID!, $comments: Int!) {
  post(id: $id) {
    id
    comments(first: $comments) { edges { node { body votesCount } } }
  }
}
"""


# ----------------------------- helpers ---------------------------------------

def load_env(path):
    """Minimal .env loader (no python-dotenv dep) — same as buzz-scan.py."""
    if not os.path.exists(path):
        return {}
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def gql(token, query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(PH_API, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Authorization", "Bearer %s" % token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit("PH API rejected the token (401). Check PH_API_TOKEN in "
                     "buzz/.env — regenerate it at "
                     "https://www.producthunt.com/v2/oauth/applications")
        if e.code == 429:
            sys.exit("PH API rate limit hit (429). The API uses a complexity "
                     "budget per 15 min — wait and re-run, or lower "
                     "ph.topics / ph.max_pages / ph.detail_fetch_top in config.yaml.")
        raise
    if data.get("errors"):
        raise RuntimeError("PH GraphQL error: %s" % json.dumps(data["errors"])[:500])
    return data["data"]


def utc_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ----------------------------- scan window ------------------------------------

PH_TZ = "America/Los_Angeles"


def ph_day_start(now=None):
    """Start of the CURRENT Product Hunt day, as a UTC datetime.

    PH's leaderboard resets at 12:01 AM Pacific and /buzz-daily fires just after
    that, so the window we want is "everything on today's board" — not a rolling
    N-hour lookback, which drags in yesterday's settled launches and buries the
    fresh ones. Anchored to midnight Pacific (the reset minute is 00:01; midnight
    is the safe boundary and cannot exclude a launch).

    Returns (utc_datetime, note) or (None, reason) when the tz database is
    unavailable — the caller then falls back to `ph.lookback_hours` rather than
    failing the run.
    """
    if ZoneInfo is None:
        return None, "zoneinfo unavailable (Python < 3.9)"
    try:
        tz = ZoneInfo(PH_TZ)
    except Exception as exc:   # ZoneInfoNotFoundError — no system tzdata
        return None, "no tz database for %s (%s)" % (PH_TZ, exc)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc), "PH day starting %s PT" % start.strftime("%Y-%m-%d")


# ----------------------------- account state ----------------------------------

def account_state(cfg):
    """PH account state for the Judge stage, read from the output JSON.

    Legacy `ph.warmup.rate_limit` values cap digest review volume only. Account
    metadata is context; it never authorizes automated posting or evasion of
    platform restrictions."""
    accounts = (cfg.get("accounts") or {}).get("ph") or []
    acc = next((a for a in accounts if a.get("status") == "active"), None)
    warm = cfg["ph"]["warmup"]
    if acc is None:
        return {"present": False, "rate_limit": warm["rate_limit"]}
    created = acc.get("created_on")
    if isinstance(created, str):
        created = datetime.strptime(created, "%Y-%m-%d").date()
    age_days = (date.today() - created).days if created else 0
    handle = (acc.get("handle") or "").strip()
    # Report missing account metadata without treating it as posting authorization.
    unset = (not handle) or handle.upper().startswith("FILL_IN")
    return {
        "present": True,
        "handle": handle or None,
        "handle_unset": unset,
        "lane": acc.get("lane"),
        "created_on": str(created),
        "age_days": age_days,
        "rate_limit": warm["rate_limit"],
    }


# ----------------------------- maker socials (API declarations only) ----------
# Untrusted profile/website URLs are never fetched. This avoids local-file reads,
# private-network requests, and redirects outside the fixed Product Hunt API.
X_NON_PROFILE = {"home", "i", "intent", "share", "search", "explore", "settings",
                 "login", "signup", "messages", "notifications", "compose",
                 "hashtag", "privacy", "tos", "about", "download"}


def fetch_maker_socials(candidates, top_n):
    """Use only the API-declared Twitter handle; preserve the call signature."""
    for candidate in candidates:
        for maker in candidate["makers"]:
            socials = {"linkedin": None, "linkedin_source": None,
                       "linkedin_company": None, "github": None,
                       "twitter": None, "twitter_source": None, "website": None}
            handle = (maker.get("twitter") or "").strip().lstrip("@")
            if re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle) and handle.lower() not in X_NON_PROFILE:
                socials["twitter"] = "https://x.com/%s" % handle
                socials["twitter_source"] = "ph_api"
            maker["socials"] = socials
            maker["reachability"] = ("reachable" if socials["twitter"] else
                                     "identity_redacted" if maker.get("name") == REDACTED else
                                     "no_handle_on_ph")


# ----------------------------- Product Hunt scan ------------------------------

def _clean_url(url):
    """Strip query/fragment from PH URLs. The API appends utm params that name
    our API application — they'd leak into Slack/CSV and destabilize the
    `ph_link + ph_profile` row key."""
    return url.split("?")[0].split("#")[0].rstrip("/") if url else url


def _user(u):
    if not u:
        return None
    username = u.get("username")
    return {
        "name": u.get("name"),
        "username": username,
        "ph_profile": _clean_url(u.get("url")) or (
            "https://www.producthunt.com/@%s" % username if username else None),
    }


def scan_ph(cfg, token, since_iso):
    ph = cfg["ph"]
    by_id = {}
    for topic in ph["topics"]:
        after = None
        for _ in range(ph["max_pages"]):
            data = gql(token, LIST_QUERY, {
                "topic": topic, "postedAfter": since_iso,
                "first": ph["posts_per_page"], "after": after,
            })
            posts = data["posts"]
            for edge in posts["edges"]:
                n = edge["node"]
                pid = n["id"]
                topics = [t["node"]["name"] for t in n["topics"]["edges"]]
                if pid not in by_id:
                    makers = []
                    for m in n.get("makers") or []:
                        mu = _user(m)
                        mu["headline"] = m.get("headline")
                        mu["twitter"] = m.get("twitterUsername")
                        mu["website"] = m.get("websiteUrl")
                        makers.append(mu)
                    by_id[pid] = {
                        "id": "ph:%s" % pid,
                        "ph_id": pid,
                        "platform": "ph",
                        "name": n["name"],
                        "tagline": n.get("tagline") or "",
                        "description_excerpt": (n.get("description") or "")[:600],
                        "ph_url": _clean_url(n["url"]),
                        "website": n.get("website"),
                        "votes_count": n.get("votesCount") or 0,
                        "comments_count": n.get("commentsCount") or 0,
                        "created_at": n.get("createdAt"),
                        "topics": topics,
                        "matched_keywords": [],
                        "makers": makers,
                        # Anonymous thread text — see fetch_details. This is what
                        # the Judge grounds a comment draft in now that PH pages
                        # are behind a bot-mitigation challenge.
                        "discussion": [],
                    }
            if not posts["pageInfo"]["hasNextPage"]:
                break
            after = posts["pageInfo"]["endCursor"]
            time.sleep(0.5)  # be polite to the complexity budget
        time.sleep(0.5)

    # Filter on configured topic/keyword fit. A newly opened board may have zero votes.
    kws = [k.lower() for k in ph["keywords"]]
    fit_topics = {t.lower().replace("-", " ") for t in (ph.get("fit_topics") or [])}
    require_fit = ph.get("require_fit", False)
    out = []
    for c in by_id.values():
        if c["votes_count"] < ph.get("min_votes", 0):
            continue
        hay = " ".join([c["name"], c["tagline"], c["description_excerpt"],
                        " ".join(c["topics"])]).lower()
        c["matched_keywords"] = [k for k in kws if k in hay]
        topics_lower = {t.lower() for t in c["topics"]}
        c["fit_topics_matched"] = sorted(topics_lower & fit_topics)
        if require_fit and not (c["matched_keywords"] or c["fit_topics_matched"]):
            continue
        out.append(c)
    return out


def fetch_details(cfg, token, candidates):
    """Fetch anonymous discussion context; never collect voters or commenter profiles."""
    ph = cfg["ph"]
    for c in candidates[:ph["detail_fetch_top"]]:
        try:
            data = gql(token, DETAIL_QUERY, {
                "id": c["ph_id"], "comments": ph["comments_limit"],
            })
        except SystemExit:
            raise
        except Exception as e:
            print("  [ph] detail fetch failed for %s: %s" % (c["name"], e),
                  file=sys.stderr)
            continue
        post = data.get("post") or {}
        for edge in (post.get("comments") or {}).get("edges", []):
            node = edge["node"]
            body = (node.get("body") or "").strip()
            if body:
                c["discussion"].append({"body": body[:1200],
                                         "votes": node.get("votesCount") or 0})
        time.sleep(0.5)


def run_scan(cfg, args):
    ph = cfg.get("ph")
    if not ph:
        sys.exit("No `ph` block in buzz/config.yaml — nothing to do.")

    env = load_env(ENV_PATH)
    token_var = ph["token_env"]
    token = env.get(token_var) or os.environ.get(token_var)
    if not token:
        sys.exit("Missing Product Hunt API token.\n"
                 "  → add `%s=<token>` to buzz/.env (gitignored),\n"
                 "    or export it as an environment variable (container runs)\n"
                 "  → get a developer token at "
                 "https://www.producthunt.com/v2/oauth/applications\n"
                 "No scan was run. The existing HN/Reddit modes are unaffected."
                 % token_var)

    out_dir = os.path.join(REPO, cfg["output_dir"])
    os.makedirs(out_dir, exist_ok=True)
    # Shared with buzz-scan.py: seen.json is git-tracked state (state_dir), not
    # scratch — a fresh container clone must not lose dedupe history.
    state_dir = os.path.join(REPO, cfg.get("state_dir", cfg["output_dir"]))
    os.makedirs(state_dir, exist_ok=True)
    seen_path = os.path.join(state_dir, "seen.json")
    legacy_seen_path = os.path.join(out_dir, "seen.json")
    seen = set()
    if not args.no_dedup:
        if os.path.exists(seen_path):
            seen = set(json.load(open(seen_path)))
        if os.path.exists(legacy_seen_path):  # absorb pre-move local history
            seen |= set(json.load(open(legacy_seen_path)))

    # Window. `ph_day` is the default: only launches on TODAY's board, so the
    # digest reaches makers while they are still watching the page. `lookback`
    # (or --full-lookback, or a missing tz database) restores the old rolling
    # ph.lookback_hours behaviour.
    # --lookback-hours N widens AND overrides the hours: the Monday routine run
    # passes 72 so the window reaches back to Friday's fire time and both weekend
    # boards fall inside it. Plain --full-lookback (48h from a Monday fire that
    # lands 5-65 min into the PH day) would cut off the first 5-65 min of
    # Saturday's board every week.
    if args.lookback_hours:
        ph["lookback_hours"] = args.lookback_hours
    widen = args.full_lookback or bool(args.lookback_hours)
    window_mode = "lookback" if widen else ph.get("window", "ph_day")
    window_note = None
    since_dt = None
    if window_mode == "ph_day":
        since_dt, note = ph_day_start()
        if since_dt is None:
            print("  ⚠ PH-day window unavailable: %s — falling back to the "
                  "%dh lookback" % (note, ph["lookback_hours"]), file=sys.stderr)
            window_mode = "lookback"
        else:
            window_note = note
    if since_dt is None:
        since_dt = datetime.now(timezone.utc) - timedelta(hours=ph["lookback_hours"])
        window_note = "rolling %dh lookback" % ph["lookback_hours"]
    since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    print("Scanning Product Hunt (%s, since %s, %d topics)..."
          % (window_note, since_iso, len(ph["topics"])))
    candidates = scan_ph(cfg, token, since_iso)

    fresh = [c for c in candidates if c["id"] not in seen]
    # Rank on RELEVANCE, then reachability. Vote count is deliberately absent:
    # it used to be a `-votes_count` term that preferred the smaller launch, but
    # (a) we now engage with every launch in our space, big or small, and (b) at
    # PH-day-open every launch has ~0 votes, so the term is noise either way.
    # Engagement is the last tiebreak only so a launch with some discussion beats
    # a totally silent one.
    def rank(c):
        reachable = any((m.get("twitter") or "").strip() for m in c["makers"])
        return (len(c["matched_keywords"]),
                len(c.get("fit_topics_matched") or []),
                1 if reachable else 0,
                c["comments_count"])
    fresh.sort(key=rank, reverse=True)

    fetch_details(cfg, token, fresh)
    print("Fetching self-declared maker socials (PH profile + own website)...")
    fetch_maker_socials(fresh, ph["detail_fetch_top"])

    account = account_state(cfg)
    out_path = os.path.join(out_dir, "ph-candidates-%s.json" % utc_today())
    # Thin-day verdict travels in the payload so the PDF cover can show it — the
    # renderer reads no config, so it cannot recompute the floor itself.
    floor = ph.get("thin_day_floor", 5)
    thin_day = window_mode == "ph_day" and len(fresh) < floor
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": window_mode,
        "window_note": window_note,
        "window_since": since_iso,
        "lookback_hours": ph["lookback_hours"],
        "thin_day": thin_day,
        "thin_day_floor": floor,
        "relevance_threshold": ph["relevance_threshold"],
        "max_cards_per_digest": ph["max_cards_per_digest"],
        "account": account,
        "counts": {
            "total": len(fresh),
            "keyword_matched": len([c for c in fresh if c["matched_keywords"]]),
            "with_details": min(len(fresh), ph["detail_fetch_top"]),
        },
        "candidates": fresh,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)

    # `--peek` is the dry-run half of dedupe: filter against the seen-log (so the
    # preview shows what a real run would surface) but never record these ids, so
    # a preview cannot consume tomorrow's candidates. `--no-dedup` is the other
    # axis (ignore the log entirely) and also implies no write.
    if not args.no_dedup and not args.peek:
        seen.update(c["id"] for c in fresh)
        with open(seen_path, "w") as f:
            json.dump(sorted(seen), f)

    print("\n%d fresh PH candidates → %s" % (len(fresh), out_path))
    # A same-day window can legitimately be thin, but it can also mean the board
    # had not filled yet when we fired. Say so rather than shipping a quiet digest
    # that reads identical to a broken scan.
    if thin_day:
        print("  ⚠ thin day: %d candidates on today's PH board (floor %d). Either "
              "the board had not filled at scan time, or it was genuinely quiet. "
              "Re-run with --full-lookback to widen to %dh."
              % (len(fresh), floor, ph["lookback_hours"]))
    if args.peek:
        print("  ⓘ --peek: seen-log NOT written — these launches will resurface "
              "on the next real run")
    print("  keyword-matched: %d | details fetched for top %d"
          % (payload["counts"]["keyword_matched"], payload["counts"]["with_details"]))
    # Social hit rate — Phase 6 of /buzz --mode ph reports these.
    scanned = [m for c in fresh[:ph["detail_fetch_top"]] for m in c["makers"]]
    if scanned:
        s = [m.get("socials") or {} for m in scanned]
        print("  makers: %d | twitter %d (api %d) | linkedin %d"
              % (len(scanned),
                 sum(1 for x in s if x.get("twitter")),
                 sum(1 for x in s if x.get("twitter_source") == "ph_api"),
                 sum(1 for x in s if x.get("linkedin"))))
    if not account.get("present"):
        print("  ⚠ no active PH account in config.yaml → accounts.ph")
    elif account.get("handle_unset"):
        print("  ⚠ PH handle is unset (accounts.ph.handle is a placeholder) — "
              "drafts have nowhere to be posted from until it is filled in")
    for c in fresh[:8]:
        # No vote count: at PH-day-open it is 0 for everything and printing it
        # invites the old "prefer the smaller launch" reflex. Fit is the signal.
        kws = ",".join(c["matched_keywords"][:3]) or \
            ",".join(c.get("fit_topics_matched") or []) or "topic-only"
        print("  • %s — %s  (%s)" % (c["name"], c["tagline"][:60], kws))


# ----------------------------- main -------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-dedup", action="store_true",
                    help="scan: ignore seen.json (testing)")
    ap.add_argument("--peek", action="store_true",
                    help="scan: dry-run — honour seen.json when filtering but do "
                         "NOT write it, so a preview can't consume candidates")
    ap.add_argument("--full-lookback", action="store_true",
                    help="scan: ignore the current-PH-day window and use "
                         "ph.lookback_hours instead (testing / thin-day recovery)")
    ap.add_argument("--lookback-hours", type=int, metavar="N",
                    help="scan: like --full-lookback but with N hours instead of "
                         "ph.lookback_hours. The daily routine passes 72 on "
                         "Mondays so the window reaches Friday's fire time and "
                         "both weekend boards are covered whole.")
    ap.add_argument("--config", default=CONFIG_PATH,
                    help="config path (default: $BUZZ_CONFIG or <project>/buzz/config.yaml)")
    args = ap.parse_args()

    with open(require_config(args.config)) as f:
        cfg = yaml.safe_load(f)
    if not cfg.get("ph"):
        sys.exit("No `ph` block in buzz/config.yaml — nothing to do.")

    run_scan(cfg, args)


if __name__ == "__main__":
    main()
