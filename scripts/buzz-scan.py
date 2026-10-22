#!/usr/bin/env python3
"""
buzz-scan.py — Community Engagement Engine, Stage 1 (fetch).

Scans Hacker News (Algolia API, no auth) and Reddit (public RSS, no auth) for threads
matching our topic keywords, dedups against the git-tracked seen-log (config
`state_dir`, default buzz/state/seen.json), and writes candidates to
buzz/working/candidates-<date>.json for the Judge stage to score + draft.

Deterministic. No judgment, no drafting, no posting. Zero external deps except PyYAML.

There are two modes:
  --mode discover  (default) — the engagement-candidate scan described above.
  --mode brands              — a READ-ONLY brand-monitoring report. Counts how
      often OTHERS mention your brand + named competitors across HN + Reddit and
      computes Share of Voice. It never drafts, never suggests engagement, and
      never touches seen.json/candidates — fully compatible with the engine's
      "never name our product when posting" rule (measuring mentions is not posting).
      Writes buzz/working/brand-mentions-<date>.json. Reports brand-mention frequency
      and Share of Voice.

Usage:
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-scan.py                 # scan both platforms
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-scan.py --platform hn   # HN only (no creds needed)
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-scan.py --platform reddit
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-scan.py --no-dedup      # ignore seen-log (testing)
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-scan.py --mode brands   # brand-mention + SoV report

No credentials needed for either platform (HN = Algolia public API, Reddit =
public RSS). buzz/.env is only used by buzz-ph-scan.py (PH_API_TOKEN).
"""

import argparse
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import yaml

from buzzpaths import REPO, CONFIG_PATH, require_config
# Identify the application honestly; Reddit requests use the operator's identity.
USER_AGENT = "buzz-engine/0.1"


# ----------------------------- helpers ---------------------------------------

def load_env(path):
    """Minimal .env loader (no python-dotenv dep)."""
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


def http_json(url, headers=None, data=None, method=None, timeout=20):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", USER_AGENT)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def all_keywords(cfg):
    """Flat list of (keyword, layer) across all layers."""
    out = []
    for layer, kws in cfg["keywords"].items():
        for kw in kws:
            out.append((kw, layer))
    return out


# ----------------------------- Hacker News ------------------------------------

def reply_window(cfg, ctype, num_comments, age_hours):
    """Score how likely a first comment on this thread is to be READ (0-10).

    Relevance ranking answers "is this our space"; it cannot answer "is there
    room for a comment here". A 235-point thread with 109 comments is a perfect
    reading signal and a poor place to join a discussion — a new comment lands at the bottom
    of page 2. This is the missing axis, and it is deliberately deterministic
    (no LLM): the same thread scores the same twice, and every point is traceable
    to a printed reason.

    Three components, because three things decide whether a reply is seen:
      * freshness (0-4) — HN orders a thread's top level by score, but a comment
        arriving hours late starts from nothing while early ones have collected
        votes. Early is the single biggest lever.
      * room (0-3) — few existing comments means the first page is not full.
      * invitation (0-3) — Ask HN exists to be answered, and a Show HN author is
        watching their thread. A plain story invites nothing.

    Returns (score, verdict, reasons). `verdict` is the one-word state for the
    card; `reasons` is what the PDF prints so a reader can disagree with it."""
    cc = (cfg.get("hn") or {}).get("comment_candidates") or {}
    ideal = cc.get("ideal_age_hours", 4)
    max_age = cc.get("max_age_hours", 12)
    max_comments = cc.get("max_comments", 60)
    reasons = []

    if age_hours is None:            # Algolia gave us no timestamp
        fresh, why = 1, "age unknown"
    elif age_hours <= ideal:
        fresh, why = 4, "%.1fh old — early, a comment can still collect votes" % age_hours
    elif age_hours <= max_age:
        fresh, why = 2, "%.1fh old — past the early window but still moving" % age_hours
    elif age_hours <= 24:
        fresh, why = 1, "%.1fh old — late, expect little traffic" % age_hours
    else:
        fresh, why = 0, "%.0fh old — settled, a new comment is unlikely to be read" % age_hours
    reasons.append(why)

    if num_comments <= 10:
        room, why = 3, "%d comments — front of the thread" % num_comments
    elif num_comments <= 25:
        room, why = 2, "%d comments — still on the first screen" % num_comments
    elif num_comments <= max_comments:
        room, why = 1, "%d comments — crowded, needs a strong opener" % num_comments
    else:
        room, why = 0, "%d comments — a first comment is buried here" % num_comments
    reasons.append(why)

    if ctype == "ask_hn":
        invite, why = 3, "Ask HN — the thread exists to be answered"
    elif ctype == "show_hn":
        invite, why = 2, "Show HN — the author is watching their own thread"
    else:
        invite, why = 0, "plain story — no built-in invitation to reply"
    reasons.append(why)

    score = fresh + room + invite
    # `crowded` is the hard stop and is checked first: it does not matter how
    # fresh or inviting a thread is once the first page of comments is full.
    if num_comments > max_comments:
        verdict = "crowded"
    elif age_hours is not None and age_hours > max_age:
        verdict = "settled"
    elif score >= cc.get("min_reply_window_score", 4):
        verdict = "open"
    else:
        verdict = "weak"
    return score, verdict, reasons


# Question/discussion shapes in a Reddit title. A thread that asks something is
# where a first comment from a new account is welcome and gets read; a bare link
# drop is not. Deliberately crude and deterministic — the same title scores the
# same twice, and the matched phrase is printed as the reason.
_RD_ASKS = (
    "how do you", "how do i", "how are people", "how are you", "how would you",
    "anyone else", "does anyone", "has anyone", "what's the best", "whats the best",
    "what is the best", "which is better", "am i the only", "is it worth",
    "any tips", "any advice", "thoughts on", "what do you think", "eli5",
)


def reddit_reply_window(cfg, sub, title, has_body, age_hours):
    """Score how likely a first comment on this Reddit thread is to be READ (0-10).

    The HN twin of this (`reply_window` above) scores freshness + room +
    invitation. Reddit's public RSS exposes neither score nor comment count, so
    **room cannot be scored at all** and is omitted rather than guessed — the same
    call buzz.md makes about not rendering those figures as 0. The third component
    is instead the sub's barrier, which is the Reddit-specific thing that decides
    whether a low-karma comment survives contact:

      * freshness (0-4)   — Reddit sorts by hot/best, and a late comment on a
        busy thread never surfaces. Same lever as HN, same weight.
      * invitation (0-3)  — a question thread exists to be answered; a self post
        means the author is present; a bare link invites nothing.
      * barrier (0-3)     — barrier:low subs do not auto-remove new-account
        comments. This is the axis HN does not have and Reddit lives or dies on.

    Returns (score, verdict, reasons). `gated` is the hard stop and is checked
    first: on a barrier:high sub it does not matter how fresh or inviting the
    thread is: community rules may restrict participation by new accounts.
    """
    cc = (cfg.get("reddit") or {}).get("comment_candidates") or {}
    ideal = cc.get("ideal_age_hours", 6)
    max_age = cc.get("max_age_hours", 24)
    reasons = []

    if age_hours is None:
        fresh, why = 1, "age unknown"
    elif age_hours <= ideal:
        fresh, why = 4, "%.1fh old — early, a comment can still collect votes" % age_hours
    elif age_hours <= max_age:
        fresh, why = 2, "%.1fh old — past the early window but still moving" % age_hours
    elif age_hours <= 48:
        fresh, why = 1, "%.1fh old — late, expect little traffic" % age_hours
    else:
        fresh, why = 0, "%.0fh old — settled, a new comment is unlikely to be read" % age_hours
    reasons.append(why)

    low = (title or "").lower()
    hit = next((a for a in _RD_ASKS if a in low), None)
    if hit or low.rstrip().endswith("?"):
        invite, why = 3, ('a question ("%s") — the thread exists to be answered' % hit
                          if hit else "a question — the thread exists to be answered")
    elif has_body:
        invite, why = 2, "self post with a body — the author is in the thread"
    else:
        invite, why = 0, "link post — no built-in invitation to reply"
    reasons.append(why)

    barrier = (sub.get("barrier") or "medium").lower()
    if barrier == "low":
        bar, why = 3, "r/%s is configured barrier:low estimate — verify current community rules" % sub["name"]
    elif barrier == "medium":
        bar, why = 1, "r/%s is configured barrier:medium estimate — verify community participation requirements" % sub["name"]
    else:
        bar, why = 0, "r/%s is configured barrier:high estimate — verify current community restrictions" % sub["name"]
    reasons.append(why)

    reasons.append("comment count unavailable — Reddit's RSS does not expose it, "
                   "so crowding is unscored. Open the thread before posting.")

    score = fresh + invite + bar
    if barrier == "high":
        verdict = "gated"
    elif age_hours is not None and age_hours > max_age:
        verdict = "settled"
    elif score >= cc.get("min_reply_window_score", 6):
        verdict = "open"
    else:
        verdict = "weak"
    return score, verdict, reasons


def scan_hn(cfg, since_ts):
    hn = cfg["hn"]
    tags = "(" + ",".join(hn["tags"]) + ")"  # OR across story/show_hn/ask_hn
    min_points = hn["min_points"]
    by_id = {}  # objectID -> candidate (accumulates matched keywords)

    for kw, layer in all_keywords(cfg):
        # Quote keywords and disable typo tolerance to avoid fuzzy false positives.
        query = '"%s"' % kw
        # Algolia HN search rejects combined numericFilters (comma-separated returns 400).
        # Use single date filter in the URL; apply min_points filter in Python below.
        params = urllib.parse.urlencode({
            "query": query,
            "tags": tags,
            "advancedSyntax": "true",
            "typoTolerance": "false",
            "hitsPerPage": 50,
        })
        url = ("https://hn.algolia.com/api/v1/search?" + params
               + "&numericFilters=created_at_i>%d" % since_ts)
        try:
            data = http_json(url)
        except Exception as e:
            print("  [hn] query '%s' failed: %s" % (kw, e), file=sys.stderr)
            continue
        for h in data.get("hits", []):
            oid = h.get("objectID")
            if not oid:
                continue
            htags = h.get("_tags", [])
            if "show_hn" in htags:
                ctype = "show_hn"
            elif "ask_hn" in htags:
                ctype = "ask_hn"
            else:
                ctype = "story"
            points = h.get("points") or 0
            if points < min_points:
                continue
            if oid not in by_id:
                ncomments = h.get("num_comments") or 0
                created_i = h.get("created_at_i")
                age_h = (round((time.time() - created_i) / 3600.0, 1)
                         if created_i else None)
                rw_score, rw_verdict, rw_reasons = reply_window(
                    cfg, ctype, ncomments, age_h)
                by_id[oid] = {
                    "id": "hn:%s" % oid,
                    "platform": "hn",
                    "type": ctype,
                    "title": h.get("title") or h.get("story_title") or "",
                    "url": "https://news.ycombinator.com/item?id=%s" % oid,
                    "external_url": h.get("url"),
                    "text_excerpt": (h.get("story_text") or h.get("comment_text") or "")[:600],
                    "points": points,
                    "num_comments": ncomments,
                    "author": h.get("author"),
                    "created_at": h.get("created_at"),
                    "age_hours": age_h,
                    "matched_keywords": [],
                    # raw signal — Judge decides UPVOTE vs COMMENT vs SKIP
                    "vote_candidate": points >= hn["vote_candidate_min_points"],
                    # Where a reply would land. Separate from relevance on
                    # purpose: `vote_candidate` rewards a big thread, this
                    # rewards a reachable one, and the two rarely agree.
                    "reply_window": {
                        "score": rw_score,
                        "verdict": rw_verdict,
                        "reasons": rw_reasons,
                    },
                    # The Judge still makes the call; this is the shortlist it
                    # picks from, so a crowded thread is never offered as one.
                    "comment_candidate": rw_verdict == "open",
                }
            mk = by_id[oid]["matched_keywords"]
            if [kw, layer] not in mk:
                mk.append([kw, layer])
        time.sleep(0.2)  # be polite to Algolia

    return list(by_id.values())


# ----------------------------- Reddit (public RSS, no auth) -------------------
# Public Atom/RSS feeds omit score and comment count. Respect access denials.
ATOM = "{http://www.w3.org/2005/Atom}"


def reddit_user_agent(value):
    """Require an honest, operator-provided identity before any Reddit request."""
    if not isinstance(value, str) or not value.strip() or "FILL_IN" in value:
        raise ValueError("Set reddit.user_agent to your application's identifying User-Agent before scanning Reddit.")
    return value.strip()


def _strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def _iso_to_ts(s):
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:
        return None


class _NotRealFeed(Exception):
    """Raised when Reddit returns 200 but the body isn't an Atom feed."""


class _Throttled(Exception):
    """A Reddit rate limit shared by every feed in the current run."""
    def __init__(self, msg, retry_after=None):
        super().__init__(msg)
        self.retry_after = retry_after


def _rf_cfg(cfg):
    """Reddit fetch tunables and required operator identity."""
    f = ((cfg or {}).get("reddit") or {}).get("fetch") or {}
    return {
        "user_agent": reddit_user_agent(((cfg or {}).get("reddit") or {}).get("user_agent")),
        "attempts": f.get("attempts", 3),
        "backoff_base_s": f.get("backoff_base_s", 4),
        "max_backoff_s": f.get("max_backoff_s", 60),
        # Legacy respect_retry_after is intentionally ignored: server delays are mandatory.
        "max_total_wait_s": f.get("max_total_wait_s", 300),
        "max_retry_after_s": f.get("max_retry_after_s", 120),
        "pace_s": f.get("pace_s", 1.2),
        "pace_jitter_s": f.get("pace_jitter_s", 0.6),
        "throttle_streak_limit": f.get("throttle_streak_limit", 4),
        "deferred_sweep": f.get("deferred_sweep", True),
        "deferred_sweep_wait_s": f.get("deferred_sweep_wait_s", 45),
    }


def _retry_after_s(err, rf):
    """Return the complete server delay, including HTTP-date values; never cap it."""
    raw = err.headers.get("Retry-After") if err.headers else None
    if not raw:
        return None
    try:
        delay = float(str(raw).strip())
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(raw))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            delay = retry_at.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0, delay)


def _stop_reddit_run(rf, reason):
    rf["_stopped"] = reason
    raise _Throttled(reason, max(0, rf.get("_next_request_at", 0) - time.monotonic()))


def _wait_for_reddit(rf, min_delay=0):
    """Honor the shared deadline or stop; wait budgets never shorten a server delay."""
    if rf.get("_stopped"):
        raise _Throttled(rf["_stopped"])
    delay = max(min_delay, rf.get("_next_request_at", 0) - time.monotonic(), 0)
    remaining = rf.setdefault("_wait_remaining_s", rf.get("max_total_wait_s", 300))
    if delay > remaining:
        _stop_reddit_run(rf, "Reddit wait exceeds the remaining run wait budget")
    if delay:
        time.sleep(delay)
        rf["_wait_remaining_s"] -= delay


def _request_reddit_rss(url, rf, attempts=None):
    """Share honest identification and bounded rate-limit handling across RSS reads."""
    user_agent = reddit_user_agent(rf.get("user_agent"))
    budget = attempts if attempts is not None else rf["attempts"]
    for attempt in range(budget):
        _wait_for_reddit(rf)
        try:
            response = urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": user_agent}), timeout=20)
            try:
                body = response.read()
                ctype = response.headers.get("Content-Type", "")
            finally:
                response.close()
            if "xml" not in ctype or not body.lstrip().startswith(b"<?xml"):
                raise _NotRealFeed("Expected an Atom feed (content-type=%r)" % ctype)
            return body
        except urllib.error.HTTPError as error:
            if error.code != 429:
                raise
            wait = _retry_after_s(error, rf)
            if wait is None:
                wait = min(rf["backoff_base_s"] * (2 ** attempt), rf["max_backoff_s"])
            rf["_next_request_at"] = max(rf.get("_next_request_at", 0),
                                          time.monotonic() + wait)
            if wait > rf["max_retry_after_s"]:
                _stop_reddit_run(rf, "Reddit Retry-After exceeds max_retry_after_s; stopping this run")
            if wait > rf.setdefault("_wait_remaining_s", rf.get("max_total_wait_s", 300)):
                _stop_reddit_run(rf, "Reddit retry exceeds the remaining run wait budget")
            if attempt == budget - 1:
                raise _Throttled("429 after %d attempt(s)" % budget, wait) from error
            print("    [reddit] rate limited; waiting %ds before attempt %d/%d"
                  % (wait, attempt + 2, budget), file=sys.stderr)
    raise RuntimeError("reddit.fetch.attempts is %r — nothing was tried" % budget)


def _fetch_rss_raw(sub, kind, rf=None, attempts=None):
    """Read a subreddit feed without impersonation or host fallback on a block."""
    return _request_reddit_rss("https://www.reddit.com/r/%s/%s/.rss?limit=50" % (sub, kind),
                               rf or _rf_cfg(None), attempts)


def reddit_rss_entries(sub, kind, rf=None, attempts=None):
    """Fetch and parse one subreddit Atom feed (kind = 'new' or 'hot')."""
    feed = ET.fromstring(_fetch_rss_raw(sub, kind, rf, attempts))
    out = []
    for e in feed.findall(ATOM + "entry"):
        def txt(tag):
            el = e.find(ATOM + tag)
            return el.text if el is not None else None
        link_el = e.find(ATOM + "link")
        author_el = e.find(ATOM + "author")
        author = None
        if author_el is not None:
            n = author_el.find(ATOM + "name")
            author = n.text if n is not None else None
        created = txt("published") or txt("updated")
        out.append({
            "id": txt("id"),                       # reddit fullname, e.g. t3_abc
            "title": (txt("title") or "").strip(),
            "link": link_el.get("href") if link_el is not None else None,
            "text": _strip_html(txt("content")),
            "author": author,
            "created_at": created,
            "created_ts": _iso_to_ts(created),
        })
    return out


def _harvest(cfg, sub, entries, since_ts, kw_layer, kws, by_id):
    """Filter one sub's feed entries into `by_id`. Split out of scan_reddit so the
    main pass and the deferred sweep share exactly one copy of the matching rules."""
    for en in entries:
        fid = en["id"]
        if not fid or fid in by_id:
            continue
        if en["created_ts"] and en["created_ts"] < since_ts:
            continue
        hay = (en["title"] + " " + en["text"]).lower()
        hits = [(k, kw_layer[k]) for k in kws if k in hay]
        if not hits:
            continue
        age_h = (round((time.time() - en["created_ts"]) / 3600, 1)
                 if en["created_ts"] else None)
        rw_score, rw_verdict, rw_reasons = reddit_reply_window(
            cfg, sub, en["title"], bool((en["text"] or "").strip()), age_h)
        by_id[fid] = {
            "id": "reddit:%s" % fid,
            "platform": "reddit",
            "subreddit": sub["name"],
            "tier": sub.get("tier"),
            "barrier": sub.get("barrier"),
            "type": "reddit",
            "title": en["title"],
            "url": en["link"],
            "author": en["author"],
            "text_excerpt": en["text"][:400],
            "created_at": en["created_at"],
            "age_hours": age_h,
            # Where a comment would LAND, scored separately from relevance —
            # the same split as HN. See reddit_reply_window() for the components
            # and why `room` is absent.
            "reply_window": {
                "score": rw_score,
                "verdict": rw_verdict,
                "reasons": rw_reasons,
            },
            "comment_candidate": rw_verdict == "open",
            "matched_keywords": hits,
        }


def scan_reddit(cfg, since_ts):
    """Scan every configured sub's `new` and `hot` feeds.

    Returns `(candidates, diagnostics)` so an unavailable feed is distinguished
    from a quiet one in the digest.
    """
    rf = _rf_cfg(cfg)
    kw_layer = {kw.lower(): layer for kw, layer in all_keywords(cfg)}
    kws = list(kw_layer.keys())
    by_id = {}
    feeds = [(sub, kind) for sub in cfg["subreddits"] for kind in ("new", "hot")]
    status = {}          # "r/sub new" -> "ok" | "throttled" | "failed: ..."
    throttled = []       # (sub, kind) queued for the deferred sweep
    streak = 0           # consecutive throttled feeds — trips the breaker
    tripped = False

    def pace():
        time.sleep(rf["pace_s"] + random.uniform(0, rf["pace_jitter_s"]))

    for sub, kind in feeds:
        key = "r/%s %s" % (sub["name"], kind)
        # Circuit breaker, mirroring BLOCK_STREAK_LIMIT in buzz-ph-scan.py. Once
        # we are demonstrably throttled, spending the full attempt budget on
        # every remaining feed adds load and wins nothing: it is what turned one
        # 429 into half the board. Queue the rest for the sweep instead.
        if tripped or rf.get("_stopped"):
            status[key] = "throttled"
            throttled.append((sub, kind))
            continue
        try:
            _harvest(cfg, sub, reddit_rss_entries(sub["name"], kind, rf),
                     since_ts, kw_layer, kws, by_id)
            status[key] = "ok"
            streak = 0
        except _Throttled:
            status[key] = "throttled"
            throttled.append((sub, kind))
            streak += 1
            if streak >= rf["throttle_streak_limit"]:
                tripped = True
                print("  [reddit] %d feeds throttled in a row — pausing the main "
                      "pass and deferring the rest to the end-of-pass sweep"
                      % streak, file=sys.stderr)
        except Exception as e:
            status[key] = "failed: %s" % e
            print("  [reddit] %s rss failed: %s" % (key, e), file=sys.stderr)
            streak = 0
        pace()

    # Deferred sweep. A 429 is a timed limit, so the feed is throttled rather
    # than gone: retried after a wait, most come back. One attempt each, paced,
    # so a still-throttled Reddit cannot make the sweep run away.
    swept_ok = 0
    if throttled and rf["deferred_sweep"] and not rf.get("_stopped"):
        print("  [reddit] %d feed(s) throttled — waiting %ds, then retrying each once"
              % (len(throttled), rf["deferred_sweep_wait_s"]), file=sys.stderr)
        try:
            _wait_for_reddit(rf, rf["deferred_sweep_wait_s"])
        except _Throttled:
            pass  # The run is stopped; preserve every unavailable-feed diagnostic.
        for sub, kind in throttled:
            if rf.get("_stopped"):
                break
            key = "r/%s %s" % (sub["name"], kind)
            try:
                _harvest(cfg, sub, reddit_rss_entries(sub["name"], kind, rf, attempts=1),
                         since_ts, kw_layer, kws, by_id)
                status[key] = "ok (sweep)"
                swept_ok += 1
            except _Throttled:
                status[key] = "throttled (sweep failed)"
            except Exception as e:
                status[key] = "failed in sweep: %s" % e
            pace()

    ok = [k for k, v in status.items() if v.startswith("ok")]
    still = sorted(k for k, v in status.items() if v.startswith("throttled"))
    failed = sorted(k for k, v in status.items() if not v.startswith("ok"))
    subs_lost = sorted({k.split()[0] for k in failed}
                       - {k.split()[0] for k in ok})
    diagnostics = {
        "feeds_total": len(feeds),
        "feeds_ok": len(ok),
        "feeds_throttled": len(still),
        "feeds_recovered_by_sweep": swept_ok,
        "breaker_tripped": tripped,
        "rate_limit_stop_reason": rf.get("_stopped"),
        # Subs with no successful feed at all. These are the ones actually
        # absent from the digest, which is the number a reader needs — a sub
        # whose `hot` 429'd but whose `new` came through is still represented.
        "subs_missing": subs_lost,
        "per_feed": status,
    }
    return list(by_id.values()), diagnostics

# ----------------------------- Brand monitoring (read-only reporting) ---------
# Counts how often OTHERS mention your brand + named competitors, then computes Share
# of Voice. REPORTING ONLY: no drafting, no engagement suggestions, no writes to
# seen.json/candidates, no vote/comment signals. This is compatible with the
# engine's "never name our product when posting" rule — counting third-party mentions
# is measurement, not posting. LinkedIn/press aren't scriptable here; the report
# flags them as manual-tracking gaps rather than silently implying full coverage.

def _reddit_search_t(window_days):
    """Map a day window to Reddit search's coarse `t` bucket (over-fetch, then
    filter precisely by created_ts)."""
    if window_days <= 1:
        return "day"
    if window_days <= 7:
        return "week"
    if window_days <= 31:
        return "month"
    if window_days <= 366:
        return "year"
    return "all"


def _alias_or_expr(aliases):
    """Join aliases into one search expression: each phrase quoted, OR-combined.
    Quoting forces exact-phrase matching (no prefix/typo fuzz on either engine)."""
    return " OR ".join('"%s"' % a for a in aliases)


def scan_hn_brand(aliases, since_ts):
    """Distinct in-window HN items (stories + comments) mentioning the brand.

    ONE Algolia query per brand (aliases OR-combined). Searches all of HN (no tag
    filter) so mentions in comments count. `typoTolerance=false` + quoted phrases
    stop Algolia matching unrelated words that share a prefix with a brand.
    Records Algolia's nbHits + a truncation flag so the count stays auditable.
    """
    params = urllib.parse.urlencode({
        "query": _alias_or_expr(aliases),
        "advancedSyntax": "true",
        "typoTolerance": "false",
        "hitsPerPage": 100,
    })
    url = ("https://hn.algolia.com/api/v1/search?" + params
           + "&numericFilters=created_at_i>%d" % since_ts)
    try:
        data = http_json(url)
    except Exception as e:
        print("  [hn-brand] query failed: %s" % e, file=sys.stderr)
        return {"count": 0, "examples": [], "algolia_nb_hits_sum": 0,
                "truncated": False, "error": str(e)}
    nb = data.get("nbHits", 0)
    hits = data.get("hits", [])
    ids, examples = set(), []
    for h in hits:
        oid = h.get("objectID")
        if not oid or oid in ids:
            continue
        ids.add(oid)
        if len(examples) < 5:
            examples.append({
                "source": "hn",
                "url": "https://news.ycombinator.com/item?id=%s" % oid,
                "title": (h.get("title") or h.get("story_title") or "")[:120],
                "created_at": h.get("created_at"),
            })
    time.sleep(0.3)  # be polite to Algolia
    return {"count": len(ids), "examples": examples,
            "algolia_nb_hits_sum": nb,
            "truncated": nb > len(hits)}  # week exceeded page cap → count is a floor


def _fetch_search_rss_raw(query, t, user_agent=None, rf=None):
    """Search follows the same identity and retry policy as subreddit discovery."""
    rf = rf or _rf_cfg({"reddit": {"user_agent": user_agent}})
    params = urllib.parse.urlencode({"q": query, "sort": "new", "t": t, "limit": 100})
    return _request_reddit_rss("https://www.reddit.com/search.rss?%s" % params, rf)


def scan_reddit_all_brands(brands, since_ts, t, user_agent=None, rf=None):
    """All brands in ONE combined Reddit search, bucketed locally by alias.

    Reddit's search RSS is aggressively rate-limited, so one request for the whole
    brand set is the difference between working and all-429. A post that mentions
    two brands counts for both (SoV is per-brand share, not a partition).

    We re-check the alias against the post TITLE only, not the body. Reddit's Atom
    `content` embeds boilerplate (the subreddit name, "submitted by …") — e.g. a
    subreddit name can contain a brand alias and be miscounted. Title-match kills that
    contamination; it is the deliberate precision-over-recall choice.
    Returns {brand: {count, examples[, error]}}.
    """
    out = {b: {"count": 0, "examples": []} for b in brands}
    all_aliases = [a for aliases in brands.values() for a in aliases]
    try:
        feed = ET.fromstring(_fetch_search_rss_raw(_alias_or_expr(all_aliases), t, user_agent, rf))
    except Exception as e:
        print("  [reddit-brand] combined search rss failed: %s" % e, file=sys.stderr)
        for b in out:
            out[b]["error"] = str(e)
        return out
    lowered = {b: [a.lower() for a in aliases] for b, aliases in brands.items()}
    seen = {b: set() for b in brands}
    for entry in feed.findall(ATOM + "entry"):
        def txt(tag):
            el = entry.find(ATOM + tag)
            return el.text if el is not None else None
        fid = txt("id")
        if not fid:
            continue
        created = txt("published") or txt("updated")
        cts = _iso_to_ts(created)
        if cts and cts < since_ts:
            continue
        title = (txt("title") or "").strip()
        hay = title.lower()  # title only — body embeds subreddit-name boilerplate
        for b in brands:
            if fid in seen[b] or not any(a in hay for a in lowered[b]):
                continue
            seen[b].add(fid)
            out[b]["count"] += 1
            if len(out[b]["examples"]) < 5:
                link_el = entry.find(ATOM + "link")
                out[b]["examples"].append({
                    "source": "reddit",
                    "url": link_el.get("href") if link_el is not None else None,
                    "title": title[:120],
                    "created_at": created,
                })
    return out


def run_brand_monitor(cfg, args):
    bm = cfg.get("brand_monitor")
    if not bm:
        print("No `brand_monitor` block in config.yaml — nothing to do.",
              file=sys.stderr)
        return
    window_days = bm.get("window_days", 7)
    since_ts = int(time.time()) - window_days * 86400
    t = _reddit_search_t(window_days)
    brands = bm["brands"]
    our = bm.get("our_brand", "ours")
    sources = [s for s in ("hn", "reddit")
               if args.platform in (s, "all")]

    print("Brand monitor: %d brands over last %dd (%s)..."
          % (len(brands), window_days, "+".join(sources)))

    # Reddit: one combined, bucketed request for the whole brand set (rate limits).
    reddit_res = scan_reddit_all_brands(
        brands, since_ts, t, reddit_user_agent((cfg.get("reddit") or {}).get("user_agent")),
        _rf_cfg(cfg)
    ) if "reddit" in sources else {}

    results = {}
    for brand, aliases in brands.items():
        hn = {"count": 0, "examples": [], "algolia_nb_hits_sum": 0, "truncated": False}
        if "hn" in sources:
            hn = scan_hn_brand(aliases, since_ts)
        rd = reddit_res.get(brand, {"count": 0, "examples": []})
        errors = {}
        if hn.get("error"):
            errors["hn"] = hn["error"]
        if rd.get("error"):
            errors["reddit"] = rd["error"]
        results[brand] = {
            "aliases": aliases,
            "hn": hn["count"],
            "reddit": rd["count"],
            "total": hn["count"] + rd["count"],
            "hn_truncated": hn["truncated"],
            "hn_algolia_nb_hits_sum": hn["algolia_nb_hits_sum"],
            "errors": errors,  # non-empty → this brand's count is incomplete this run
            "examples": (hn["examples"] + rd["examples"])[:6],
        }
        print("  • %-10s hn=%-3d reddit=%-3d total=%d"
              % (brand, results[brand]["hn"], results[brand]["reddit"],
                 results[brand]["total"]))

    grand = sum(r["total"] for r in results.values())
    for r in results.values():
        r["share_of_voice_pct"] = round(100.0 * r["total"] / grand, 1) if grand else 0.0

    out_dir = os.path.join(REPO, cfg["output_dir"])
    os.makedirs(out_dir, exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = os.path.join(out_dir, "brand-mentions-%s.json" % date)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "our_brand": our,
        "sources": sources,
        "coverage_note": ("HN (Algolia, stories+comments) + Reddit (public search "
                          "RSS). LinkedIn + press are NOT covered by this script — "
                          "track those manually and add to the weekly total."),
        "methodology": ("A mention = one distinct in-window HN item or Reddit post "
                        "whose text contains a brand alias. HN dedupes by objectID "
                        "across aliases; `hn_truncated` flags weeks exceeding the "
                        "100-hit page cap (count is then a floor). SoV = a brand's "
                        "total / sum of all tracked brands' totals."),
        "totals": {
            "all_mentions": grand,
            "%s_mentions" % our: results.get(our, {}).get("total", 0),
            "%s_share_of_voice_pct" % our: results.get(our, {}).get("share_of_voice_pct", 0.0),
        },
        "brands": results,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)

    print("\nShare of Voice (last %dd, %s):" % (window_days, "+".join(sources)))
    for brand, r in sorted(results.items(), key=lambda kv: kv[1]["total"], reverse=True):
        mark = " ←us" if brand == our else ""
        print("  %-10s %5.1f%%  (%d mentions)%s"
              % (brand, r["share_of_voice_pct"], r["total"], mark))
    print("\n→ %s" % out_path)


# ----------------------------- main -------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["discover", "brands"], default="discover",
                    help="discover = engagement candidates (default); "
                         "brands = read-only brand-mention + SoV report")
    ap.add_argument("--platform", choices=["hn", "reddit", "all"], default="all")
    ap.add_argument("--no-dedup", action="store_true")
    ap.add_argument("--peek", action="store_true",
                    help="dry-run: honour seen.json when filtering but do NOT "
                         "write it, so a preview can't consume candidates")
    ap.add_argument("--lookback-hours", type=int, metavar="N",
                    help="override config lookback_hours for this run. The daily "
                         "routine passes 72 on Mondays: it does not run at the "
                         "weekend, so Monday's fire is ~72h after Friday's and "
                         "the default 48h would permanently skip ~24h of threads "
                         "every week. Overlap with Friday's run is deduped by "
                         "seen.json.")
    ap.add_argument("--config", default=CONFIG_PATH,
                    help="config path (default: $BUZZ_CONFIG or <project>/buzz/config.yaml)")
    args = ap.parse_args()

    with open(require_config(args.config)) as f:
        cfg = yaml.safe_load(f)
    if args.lookback_hours:
        cfg["lookback_hours"] = args.lookback_hours

    if args.platform in ("reddit", "all"):
        try:
            reddit_user_agent((cfg.get("reddit") or {}).get("user_agent"))
        except ValueError as error:
            sys.exit(str(error))

    if args.mode == "brands":
        run_brand_monitor(cfg, args)
        return

    out_dir = os.path.join(REPO, cfg["output_dir"])
    os.makedirs(out_dir, exist_ok=True)
    # seen.json is git-tracked state (state_dir), not scratch: the daily routine
    # runs in a fresh clone, so untracked dedupe state would reset every run.
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

    since_ts = int(time.time()) - cfg["lookback_hours"] * 3600
    candidates = []
    reddit_diag = None

    if args.platform in ("hn", "all"):
        print("Scanning Hacker News (last %dh)..." % cfg["lookback_hours"])
        candidates += scan_hn(cfg, since_ts)

    if args.platform in ("reddit", "all"):
        print("Scanning Reddit via public RSS (last %dh)..." % cfg["lookback_hours"])
        reddit_cands, reddit_diag = scan_reddit(cfg, since_ts)
        candidates += reddit_cands

    fresh = [c for c in candidates if c["id"] not in seen]
    # Sort: precise-keyword matches and higher engagement float to the top.
    def rank(c):
        layers = {layer for _, layer in c["matched_keywords"]}
        precise = 1 if ("precise" in layers or "persona_pain" in layers) else 0
        eng = (c.get("points") or 0) + (c.get("score") or 0) + (c.get("num_comments") or 0)
        return (precise, eng)
    fresh.sort(key=rank, reverse=True)

    # The comment shortlist, ranked on reply window rather than relevance —
    # `rank` above sorts for reading the digest, this sorts for posting into it.
    # Emitted as ids, not a second copy of the candidates, so there is exactly
    # one record per thread and the two lists cannot drift apart.
    cc_cfg = (cfg.get("hn") or {}).get("comment_candidates") or {}
    comment_shortlist = []
    if cc_cfg.get("enabled", True):
        open_hn = [c for c in fresh
                   if c["platform"] == "hn" and c.get("comment_candidate")]
        open_hn.sort(key=lambda c: (c["reply_window"]["score"],
                                    c.get("points") or 0), reverse=True)
        comment_shortlist = [c["id"] for c in
                             open_hn[:cc_cfg.get("max_per_digest", 3)]]

    # Rank discussion signals by reply window and community participation requirements.
    # A shortlist never authorizes bypassing a community restriction.
    rcc_cfg = (cfg.get("reddit") or {}).get("comment_candidates") or {}
    reddit_shortlist = []
    if rcc_cfg.get("enabled", True):
        open_rd = [c for c in fresh
                   if c["platform"] == "reddit" and c.get("comment_candidate")]
        open_rd.sort(key=lambda c: (0 if (c.get("barrier") or "") == "low" else 1,
                                    -c["reply_window"]["score"]))
        reddit_shortlist = [c["id"] for c in
                            open_rd[:rcc_cfg.get("max_per_digest", 3)]]

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = os.path.join(out_dir, "candidates-%s.json" % date)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_hours": cfg["lookback_hours"],
        "counts": {
            "total": len(fresh),
            "hn": len([c for c in fresh if c["platform"] == "hn"]),
            "reddit": len([c for c in fresh if c["platform"] == "reddit"]),
            "vote_candidates": len([c for c in fresh if c.get("vote_candidate")]),
            "hn_comment_candidates": len([c for c in fresh
                                          if c["platform"] == "hn"
                                          and c.get("comment_candidate")]),
            "reddit_comment_candidates": len([c for c in fresh
                                              if c["platform"] == "reddit"
                                              and c.get("comment_candidate")]),
        },
        # Ranked ids of the HN threads with an open reply window. An empty list
        # is a real answer ("no open window today"), not a missing field, and
        # /buzz must say so rather than falling back to relevance ranking.
        "hn_comment_shortlist": comment_shortlist,
        # Ranked ids of Reddit threads with an open reply window. Empty is a real
        # answer ("no open window today"), not a missing field.
        "reddit_comment_shortlist": reddit_shortlist,
        "candidates": fresh,
    }
    # Per-feed scan health. Without this a throttled Reddit run and a quiet one
    # produce the same payload, so the digest cannot tell the reader which subs
    # are actually missing. Absent when Reddit was not scanned this run.
    if reddit_diag is not None:
        payload["reddit_scan"] = reddit_diag
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)

    # `--peek` is the dry-run half of dedupe: filter against the seen-log so the
    # preview matches a real run, but never record these ids, so a preview cannot
    # consume tomorrow's candidates. `--no-dedup` (ignore the log) also skips it.
    if not args.no_dedup and not args.peek:
        seen.update(c["id"] for c in fresh)
        with open(seen_path, "w") as f:
            json.dump(sorted(seen), f)

    print("\n%d fresh candidates → %s" % (len(fresh), out_path))
    if args.peek:
        print("  ⓘ --peek: seen-log NOT written — these threads will resurface "
              "on the next real run")
    print("  HN: %d | Reddit: %d | filtered signals: %d"
          % (payload["counts"]["hn"], payload["counts"]["reddit"],
             payload["counts"]["vote_candidates"]))
    if reddit_diag is not None:
        d = reddit_diag
        line = ("  reddit feeds: %d/%d ok" % (d["feeds_ok"], d["feeds_total"]))
        if d["feeds_recovered_by_sweep"]:
            line += " (%d recovered by the sweep)" % d["feeds_recovered_by_sweep"]
        if d["feeds_throttled"]:
            line += " · %d still throttled" % d["feeds_throttled"]
        print(line)
        if d["subs_missing"]:
            print("  ⚠ no feed came through for: %s — these subs are absent from "
                  "today's candidates, not quiet" % ", ".join(d["subs_missing"]))

    if rcc_cfg.get("enabled", True) and payload["counts"]["reddit"]:
        if reddit_shortlist:
            print("  reddit comment shortlist (open reply window), best first:")
            by_id_r = {c["id"]: c for c in fresh}
            for cid in reddit_shortlist:
                c = by_id_r[cid]
                print("    • [r/%s barrier:%s] %s  (window %d/10)"
                      % (c["subreddit"], c.get("barrier"), c["title"][:56],
                         c["reply_window"]["score"]))
        else:
            print("  ⓘ no Reddit thread has an open reply window today — "
                  "review the filtered reading signals and decide independently whether to participate.")

    if cc_cfg.get("enabled", True) and payload["counts"]["hn"]:
        if comment_shortlist:
            print("  comment shortlist (open reply window), best first:")
            by_id = {c["id"]: c for c in fresh}
            for cid in comment_shortlist:
                c = by_id[cid]
                print("    • [%s] %s  (window %d/10)"
                      % (c["type"], c["title"][:64], c["reply_window"]["score"]))
        else:
            print("  ⓘ no HN thread has an open reply window today — every "
                  "candidate is crowded, settled, or scored below "
                  "min_reply_window_score. Return filtered signals for independent reading.")
    for c in fresh[:8]:
        loc = c.get("subreddit", c["type"])
        kws = ",".join(k for k, _ in c["matched_keywords"][:3])
        print("  • [%s/%s] %s  (%s)" % (c["platform"], loc, c["title"][:70], kws))


if __name__ == "__main__":
    main()
