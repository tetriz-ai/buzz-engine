#!/usr/bin/env python3
"""
buzz-digest-pdf.py — render any /buzz digest as a briefing PDF.

Works for every channel: `--mode ph` (Product Hunt), `--mode hn`, `--mode reddit`,
and the default mixed HN+Reddit run. The two scanners emit different candidate
shapes, so everything is read through the c_* accessors and only the PH-specific
sections (maker context) are gated on channel. The channel is inferred from the candidates' `platform` field and can be
forced with --channel.

Output name: `<channel>-digest-DD-MM-YYYY.pdf`, e.g. ph-digest-05-08-2026.pdf,
buzz-digest-05-08-2026.pdf.

Reads the scanner's candidates JSON (facts) plus a drafts JSON written by the
Judge/Draft stage (scores, why-relevant, comment + reply drafts) and renders a
paginated document: cover, one page per carded launch, filtered signals, and the
launches that were scanned but not carded.

The document groups relevant discussions and recommended copy for human review.
It does not collect or display supporter lists.

Deliberately dependency-free: no network, and PyYAML is optional. It reads the
user's buzz/config.yaml only for the output dir and the cover checklists, and
falls back to defaults when the config or PyYAML is missing, so it runs anywhere
the scanner's output can be copied to and can be tested without a PH token. PDF conversion uses playwright if
importable, else headless Chrome; with neither, the HTML is left in place and
the path is reported (never a silent failure).

Usage:
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-digest-pdf.py --drafts /tmp/drafts.json
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-digest-pdf.py --candidates buzz/working/ph-candidates-2026-08-04.json \
      --drafts /tmp/drafts.json --out buzz/working/ph-digest-2026-08-04.pdf

--drafts carries the Judge/Draft stage's verdict on EVERY scanned launch, so the
document accounts for the whole day the way the Slack digest does (cards + the
filtered signal list + what was dropped). Preferred shape:

  {"judged": [
     {"ph_link": "https://www.producthunt.com/products/x",  # required — merge key
      "action": "card",                    # card | upvote | skip
      "relevance": 8,                      # required
      "why_relevant": "one line",
      "comment_draft": "...",
      "comment_label": "guidance",
      "grounded_in": "launch description + README (github.com/owner/repo)"   # or description-only
                     # what the DRAFT was checked against. PH default: description-only.
                     # HN/Reddit: "thread + top replies", default "RSS excerpt only".
      "makers": [{"ph_profile": "https://www.producthunt.com/@handle",  # merge key
                  "reply_draft": "...",
                  "outreach": "twitter" | "linkedin" | "none",
                  "notes": "unverified (web) - verify before sending"}]},
     {"ph_link": "...", "action": "upvote", "relevance": 7},
     {"ph_link": "...", "action": "skip", "relevance": 2, "reason": "consumer app"}]}

A bare JSON list is still accepted and treated as all-carded (the original form).
Entries whose ph_link matches no scanned launch are reported, never dropped
silently; scanned launches with no verdict are counted on the cover as a gap.
"""

import argparse
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from buzzpaths import REPO, output_dir
def _pw_browser_chromes():
    """Chromium shipped inside a playwright browsers dir, without playwright itself.

    The daily routine's container pre-installs the browser there and sets
    PLAYWRIGHT_BROWSERS_PATH, but does NOT install the python package — so the
    playwright branch below fails and the binary is invisible to a bare
    `which chromium`. Without this the digest silently degrades to HTML every day.
    """
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "", "/opt/pw-browsers",
             os.path.expanduser("~/.cache/ms-playwright")]
    found = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for pat in ("chromium-*/chrome-linux/chrome",
                    "chromium_headless_shell-*/chrome-linux/headless_shell"):
            found += sorted(glob.glob(os.path.join(root, pat)), reverse=True)
    return found


CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "chromium", "chromium-browser",
] + _pw_browser_chromes()

# ----------------------------- helpers ----------------------------------------

def e(s):
    """Escape for HTML; None becomes an empty string."""
    return html.escape(str(s), quote=True) if s not in (None, "") else ""


def show(path):
    """Repo-relative when inside the repo, absolute otherwise (a wall of ../ is
    less readable than the real path)."""
    rel = os.path.relpath(path, REPO)
    return path if rel.startswith("..") else rel


def norm(u):
    return (u or "").strip().rstrip("/").lower()


# --------------- channel normalization (ph | hn | reddit | buzz) ---------------
# The two scanners emit different shapes: PH has name/tagline/ph_url/votes_count/
# makers, HN and Reddit have title/url/text_excerpt/points|score/subreddit. Rather
# than fork the renderer, every candidate is read through these accessors, so one
# document template serves all channels and only the channel-specific SECTIONS
# (maker context) are gated.

CHANNEL_LABEL = {"ph": "Product Hunt", "hn": "Hacker News",
                 "reddit": "Reddit", "buzz": "HN + Reddit"}
# What a single item is called on each channel — used in prose, so a Reddit digest
# doesn't talk about "launches".
ITEM_WORD = {"ph": "launch", "hn": "thread", "reddit": "thread", "buzz": "thread"}


def detect_channel(payload, explicit=None):
    if explicit:
        return explicit
    plats = {(c.get("platform") or "").lower()
             for c in (payload.get("candidates") or [])}
    plats.discard("")
    if plats == {"ph"}:
        return "ph"
    if plats == {"hn"}:
        return "hn"
    if plats == {"reddit"}:
        return "reddit"
    if plats:
        return "buzz"          # mixed HN + Reddit, the default /buzz run
    return "ph"                # empty scan: assume the caller's mode


def c_title(c):
    return c.get("name") or c.get("title") or "(untitled)"


def c_sub(c):
    """Second line: PH tagline, or the first sentence of a thread's text."""
    if c.get("tagline"):
        return c["tagline"]
    txt = (c.get("text_excerpt") or "").strip().replace("\n", " ")
    return (txt[:160] + "…") if len(txt) > 160 else txt


def c_url(c):
    return c.get("ph_url") or c.get("url") or ""


def c_facts(c):
    """Engagement + context, per channel. Reddit's RSS exposes neither score nor
    comment count, so those are omitted rather than rendered as 0 — a real
    distinction the reader needs when judging."""
    f = []
    for label, key in (("votes", "votes_count"), ("points", "points"),
                       ("score", "score")):
        v = c.get(key)
        # A PH scan now lands minutes after the board opens, so `votes_count: 0`
        # means "not measured yet", not "nobody liked it". Omit it, for the same
        # reason Reddit's missing score is omitted rather than rendered as 0.
        if v is None or (key == "votes_count" and not v):
            continue
        f.append("<b>%s</b> %s" % (e(v), label))
    for key in ("comments_count", "num_comments"):
        if c.get(key) is not None:
            f.append("<b>%s</b> comments" % e(c[key]))
    if c.get("subreddit"):
        bits = "r/%s" % c["subreddit"]
        if c.get("barrier"):
            bits += " (barrier: %s)" % c["barrier"]
        f.append("<b>%s</b>" % e(bits))
    if c.get("type") and not c.get("subreddit"):
        f.append(e(str(c["type"])))
    if c.get("topics"):
        f.append(e(", ".join(c["topics"][:4])))
    if c.get("matched_keywords"):
        # Two scanners, two shapes: buzz-ph-scan.py emits flat strings, while
        # buzz-scan.py emits [keyword, layer] pairs so the digest can tell a
        # precise match from a broad one. This only ever ran on PH cards before
        # so the pair shape crashed
        # the join the first time an HN card was rendered. Take the keyword and
        # drop the layer, which is a ranking input rather than a reader-facing fact.
        kws = [k[0] if isinstance(k, (list, tuple)) and k else k
               for k in c["matched_keywords"][:4]]
        f.append("matched: <b>%s</b>" % e(", ".join(str(k) for k in kws)))
    if c.get("author"):
        f.append("by <b>%s</b>" % e(c["author"]))
    return f


def newest_candidates():
    """Newest output from EITHER scanner: buzz-ph-scan.py writes ph-candidates-*,
    buzz-scan.py writes candidates-*. Picked by mtime so the most recent scan wins
    regardless of which channel it was."""
    base = output_dir()
    files = (glob.glob(os.path.join(base, "ph-candidates-*.json"))
             + glob.glob(os.path.join(base, "candidates-*.json")))
    if not files:
        sys.exit("no candidates JSON in %s — run a scanner first, or pass "
                 "--candidates" % os.path.relpath(base, REPO))
    return max(files, key=os.path.getmtime)


def digest_date(payload, path):
    """Prefer the scan's own date; fall back to the filename. Never invent one."""
    gen = (payload.get("generated_at") or "")[:10]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", gen):
        return gen
    mo = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
    return mo.group(1) if mo else "unknown-date"


def out_name(channel, date_iso):
    """`<channel>-digest-DD-MM-YYYY.pdf`. The date is rendered day-first for
    humans, while the scan's own ISO date stays the internal source of truth."""
    y, m, d = (date_iso.split("-") + ["", "", ""])[:3]
    stamp = "%s-%s-%s" % (d, m, y) if (y and m and d) else date_iso
    return "%s-digest-%s.pdf" % (channel, stamp)


def read_judged(raw):
    """Accept either shape of --drafts:
      * a bare list — every entry is a carded launch (the original form), or
      * {"judged": [...]} — every SCANNED launch with an `action` of
        card / upvote / skip.
    The second form is preferred because it makes the document account for the
    whole day: a filtered signal or a dropped launch can no longer be silently
    absent, which is what a reader needs to tell "nothing relevant today" apart
    from "the scan broke"."""
    if isinstance(raw, dict):
        judged = raw.get("judged")
        if not isinstance(judged, list):
            sys.exit('--drafts object form needs a "judged" list')
        return judged
    if isinstance(raw, list):
        return [dict(d, action=d.get("action") or "card") for d in raw]
    sys.exit("--drafts must be a JSON list, or an object with a `judged` list")


def merge(candidates, judged):
    """Overlay judgements onto scanned launches. Returns (carded, upvote, skipped,
    missing_keys). Order within each bucket follows --drafts, which is the
    digest's own ranking."""
    by_link = {norm(c_url(c)): c for c in candidates}
    carded, upvote, skipped, missing = [], [], [], []
    for d in judged:
        cand = by_link.get(norm(d.get("ph_link")))
        if not cand:
            missing.append(d.get("ph_link"))
            continue
        action = (d.get("action") or "card").lower()
        if action != "card":
            (upvote if action == "upvote" else skipped).append({"cand": cand, "draft": d})
            continue
        makers_by_profile = {norm(m.get("ph_profile")): m for m in cand.get("makers") or []}
        makers = []
        for dm in d.get("makers") or []:
            base = dict(makers_by_profile.get(norm(dm.get("ph_profile"))) or {})
            base.update({k: v for k, v in dm.items() if v not in (None, "")})
            makers.append(base)
        if not makers:  # drafts omitted makers — still show what the scan found
            makers = list(cand.get("makers") or [])
        carded.append({"cand": cand, "draft": d, "makers": makers})
    return carded, upvote, skipped, missing


# ----------------------------- HTML rendering ---------------------------------

CSS = """
@page { size: A4; margin: 14mm 13mm 12mm 13mm; }
* { box-sizing: border-box; }
body { font: 10.5pt/1.45 -apple-system, "Helvetica Neue", Arial, sans-serif;
       color: #14161a; margin: 0; }
.page { page-break-after: always; }
.page:last-child { page-break-after: auto; }
h1 { font-size: 21pt; margin: 0 0 2mm; letter-spacing: -0.4pt; }
h2 { font-size: 14pt; margin: 0 0 1mm; letter-spacing: -0.2pt; }
h3 { font-size: 9pt; text-transform: uppercase; letter-spacing: 0.9pt;
     color: #6b7280; margin: 6mm 0 2mm; font-weight: 600; }
.sub { color: #6b7280; font-size: 9.5pt; margin: 0 0 6mm; }
.rule { height: 2px; background: #14161a; margin: 0 0 5mm; }
table { width: 100%; border-collapse: collapse; font-size: 9.5pt; }
th { text-align: left; font-size: 8pt; text-transform: uppercase;
     letter-spacing: 0.7pt; color: #6b7280; border-bottom: 1px solid #d1d5db;
     padding: 0 3mm 1.5mm 0; font-weight: 600; }
td { padding: 1.8mm 3mm 1.8mm 0; border-bottom: 1px solid #eef0f3;
     vertical-align: top; }
td.num, th.num { text-align: right; padding-right: 0; white-space: nowrap; }
td.rank, th.rank { text-align: right; width: 8mm; padding-right: 3mm;
                   color: #9ca3af; white-space: nowrap; }
.facts { color: #4b5563; font-size: 9pt; margin: 0 0 4mm; }
.facts b { color: #14161a; font-weight: 600; }
.draft { background: #f6f7f9; border-left: 3px solid #14161a;
         padding: 3mm 4mm; margin: 0 0 4mm; font-size: 10pt;
         white-space: pre-wrap; }
.draft .who { display: block; font-size: 8pt; text-transform: uppercase;
              letter-spacing: 0.7pt; color: #6b7280; margin-bottom: 1.5mm; }
.draft .grounded { display: block; margin-top: 2.5mm; padding-top: 1.8mm;
                   border-top: 1px solid #e3e5e9; font-size: 7.5pt;
                   text-transform: uppercase; letter-spacing: 0.5pt;
                   color: #6b7280; white-space: normal; }
.tag { display: inline-block; font-size: 7.5pt; text-transform: uppercase;
       letter-spacing: 0.6pt; padding: 0.6mm 2mm; border-radius: 2px;
       vertical-align: 1.2px; font-weight: 600; }
.tag.go { background: #dcfce7; color: #14532d; }
.tag.hold { background: #fef3c7; color: #713f12; }
.tag.warn { background: #fee2e2; color: #7f1d1d; }
.tag.neutral { background: #e5e7eb; color: #374151; }
.score { float: right; font-size: 12pt; font-weight: 700; color: #6b7280; }
.maker { border: 1px solid #e5e7eb; border-radius: 3px; padding: 3mm 4mm;
         margin: 0 0 3mm; }
.maker .name { font-weight: 600; }
.maker .links { font-size: 9pt; color: #4b5563; margin: 1mm 0 0; }
a { color: #14161a; text-decoration: none; border-bottom: 1px solid #c7cad0;
    word-break: break-all; }
.empty { color: #6b7280; font-size: 9pt; font-style: italic; }
.note { font-size: 8.5pt; color: #6b7280; margin: 5mm 0 0;
        border-top: 1px solid #eef0f3; padding-top: 2mm; }
.checklist { border: 1.5px solid #14161a; border-radius: 3px;
             padding: 4mm 5mm 3.5mm; margin: 0 0 6mm; }
.checklist .hd { font-size: 9pt; text-transform: uppercase; letter-spacing: 0.9pt;
             font-weight: 600; margin: 0 0 2.5mm; }
.checklist .hd.wk { color: #6b7280; margin: 4mm 0 2.5mm; }
.checklist .hd .mins { text-transform: none; letter-spacing: 0; font-weight: 400;
             color: #6b7280; }
.checklist ul { list-style: none; margin: 0; padding: 0; font-size: 9.5pt; }
.checklist li { padding: 1mm 0 1mm 6.5mm; position: relative; }
/* A drawn box, not U+2610 — the PDF font stack has no glyph for it. */
.checklist li:before { content: ""; position: absolute; left: 0; top: 1.7mm;
             width: 3.2mm; height: 3.2mm; border: 1px solid #9ca3af;
             border-radius: 1px; }
.checklist .onerule { margin: 4mm 0 0; padding-top: 2.2mm;
             border-top: 1px solid #eef0f3; font-size: 9pt; font-weight: 600; }
.queue { border: 1px solid #e5e7eb; border-radius: 3px; padding: 4mm 5mm;
         margin: 0 0 5mm; break-inside: avoid; }
.queue .qhd { font-size: 11pt; font-weight: 600; margin: 0 0 1mm; }
.queue .qnum { display: inline-block; min-width: 6mm; height: 6mm;
         line-height: 6mm; text-align: center; background: #14161a; color: #fff;
         border-radius: 50%; font-size: 8.5pt; margin-right: 1.5mm; }
.queue .qmeta { font-size: 8.5pt; color: #6b7280; margin: 0 0 3mm; }
.queue .qsteps { font-size: 9.5pt; margin: 0 0 3mm; padding-left: 6mm; }
.queue .qsteps li { padding: 0.4mm 0; }
.queue .qlabel { font-size: 7.5pt; text-transform: uppercase;
         letter-spacing: 0.7pt; color: #6b7280; margin: 0 0 1.5mm;
         font-weight: 600; }
.queue .qwhy { font-size: 8pt; text-transform: uppercase; letter-spacing: 0.6pt;
         color: #6b7280; margin: 0 0 1mm; font-weight: 600; }
.queue ul.qwhy { text-transform: none; letter-spacing: 0; font-weight: 400;
         font-size: 8.5pt; color: #4b5563; margin: 0 0 2.5mm; padding-left: 5mm; }
.queue .qgrounded { font-size: 7.5pt; text-transform: uppercase;
         letter-spacing: 0.5pt; color: #6b7280; margin: 0;
         border-top: 1px solid #eef0f3; padding-top: 1.8mm; }
.queue .warnline { font-size: 8.5pt; color: #7f1d1d; background: #fee2e2;
         padding: 2mm 3mm; border-radius: 2px; margin: 2.5mm 0 0; }
"""


# Research prompts are static and carry no activity quotas.
CHECKLISTS = {
    "hn": """<div class="checklist"><p class="hd">HN research checklist</p>
<ul><li>Read the original thread and its replies.</li>
<li>Research only: HN prohibits generated or AI-edited text. Write any contribution yourself.</li>
<li>Disclose relevant affiliation and follow HN rules.</li></ul></div>""",
    "ph": """<div class="checklist"><p class="hd">PH research checklist <span class="mins">&middot; {ph_time}</span></p>
<ul><li>Explore launches relevant to your interests.</li>
<li>Assess each independently; no voting or commenting quota.</li>
<li>Use recommended copy as guidance and write your own original content under platform rules.</li>
<li>Disclose relevant affiliation.</li></ul></div>""",
    "reddit": """<div class="checklist"><p class="hd">Reddit research checklist</p>
<ul><li>Read the thread, replies and subreddit rules.</li>
<li>Use recommended copy as guidance and write your own original content.</li>
<li>Disclose relevant affiliation and use only experience you can truthfully claim.</li>
<li>Assess relevance independently; no voting or commenting quota.</li></ul></div>""",
}


def _load_cfg():
    try:
        import yaml
        from buzzpaths import CONFIG_PATH
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def checklist_for(channel):
    """The cover checklist for a lane. `digest.checklists.<lane>` in the user's
    config replaces the default wholesale ({title, time, daily, weekly, rule});
    otherwise the default above is used, with the PH fire time taken from
    `schedule.fire_time_label`."""
    cfg = _load_cfg()
    custom = ((cfg.get("digest") or {}).get("checklists") or {}).get(channel)
    if custom:
        def ul(items):
            return "<ul>\n%s\n</ul>" % "\n".join("<li>%s</li>" % e(i) for i in items)
        parts = ['<div class="checklist">',
                 '<p class="hd">%s <span class="mins">&middot; %s</span></p>' % (
                     e(custom.get("title") or "Daily %s checklist" % channel.upper()),
                     e(custom.get("time") or "10 min")),
                 ul(custom.get("daily") or [])]
        if custom.get("weekly"):
            parts += ['<p class="hd wk">Once a week</p>', ul(custom["weekly"])]
        if custom.get("rule"):
            parts.append('<p class="onerule">%s</p>' % e(custom["rule"]))
        parts.append("</div>")
        return "\n".join(parts)
    label = (cfg.get("schedule") or {}).get("fire_time_label") or "right after the board resets"
    return CHECKLISTS.get(channel, "").replace("{ph_time}", e(label))


def _tag(label):
    lab = (label or "").lower()
    if "ready" in lab:
        return '<span class="tag go">%s</span>' % e(label)
    if "hold" in lab:
        return '<span class="tag hold">%s</span>' % e(label)
    return '<span class="tag neutral">%s</span>' % e(label or "unlabelled")


def _link(url, text=None):
    return '<a href="%s">%s</a>' % (e(url), e(text or url))


def render_scan_facts(payload, channel="ph"):
    """Window, thin-day verdict and maker hit rates, on the cover.

    buzz.md routes these OUT of the Slack summary on the premise that the PDF
    carries them (the cover includes scan diagnostics, so a
    widened Monday window or a thin board looked like an ordinary quiet day).
    Every field is optional: older payloads simply render fewer facts."""
    facts = []
    note = payload.get("window_note")
    hours = payload.get("lookback_hours")
    if not note and hours:
        note = "rolling %sh lookback" % hours
    if note:
        tag = ""
        if channel == "ph" and payload.get("window") not in (None, "ph_day"):
            # Not today's board: a --full-lookback / --lookback-hours run (Monday
            # weekend recovery, thin-day re-run) or a container with no tz data.
            tag = ' <span class="tag neutral">widened window</span>'
        since = payload.get("window_since")
        facts.append("<b>Window:</b> %s%s%s"
                     % (e(note), (" &nbsp;(since %s)" % e(since)) if since else "", tag))
    if payload.get("thin_day"):
        facts.append('<span class="tag warn">thin day</span> %d candidate(s) on '
                     "today's board, below the floor of %d &mdash; either the board "
                     "had not filled at scan time or it was genuinely quiet. Do not "
                     "read this as a normal quiet day."
                     % ((payload.get("counts") or {}).get("total", 0),
                        payload.get("thin_day_floor") or 0))
    # Reddit feed health from the scanner diagnostics.
    # Reddit's RSS 429s constantly and a throttled sub used to be indistinguishable
    # from a quiet one once the run was over, which is the same failure the thin-day
    # verdict exists to prevent on PH. `subs_missing` is the number that matters: a
    # sub whose `hot` was throttled but whose `new` came through is still represented.
    rs = payload.get("reddit_scan")
    if rs:
        line = ("<b>Reddit feeds:</b> %d/%d ok"
                % (rs.get("feeds_ok", 0), rs.get("feeds_total", 0)))
        if rs.get("feeds_recovered_by_sweep"):
            line += " &nbsp;(%d recovered by the end-of-pass sweep)" % rs["feeds_recovered_by_sweep"]
        if rs.get("feeds_throttled"):
            line += " &nbsp;&middot;&nbsp; %d still throttled" % rs["feeds_throttled"]
        if rs.get("breaker_tripped"):
            line += ' <span class="tag neutral">breaker tripped</span>'
        facts.append(line)
        if rs.get("subs_missing"):
            facts.append('<span class="tag warn">partial scan</span> no feed came '
                         "through for %s. These subs are <b>absent</b> from today's "
                         "candidates, not quiet &mdash; do not read the list below as "
                         "full coverage." % e(", ".join(rs["subs_missing"])))

    if channel == "ph":
        counts = payload.get("counts") or {}
        cands = payload.get("candidates") or []
        top = cands[:counts.get("with_details", len(cands))]
        makers = [m for c in top for m in (c.get("makers") or [])]
        if makers:
            socials = [m.get("socials") or {} for m in makers]
            tw = sum(1 for s in socials if s.get("twitter"))
            api = sum(1 for s in socials if s.get("twitter_source") == "ph_api")
            li = sum(1 for s in socials if s.get("linkedin"))
            facts.append("<b>Makers:</b> %d looked up &nbsp;&middot;&nbsp; Twitter %d "
                         "(%d from the PH API) &nbsp;&middot;&nbsp; LinkedIn %d"
                         % (len(makers), tw, api, li))
    return "<br>".join(facts) or "<span class='empty'>no scan diagnostics in this payload</span>"


def render_cover(payload, date, carded, upvote, skipped, missing,
                 channel="ph"):
    counts = payload.get("counts") or {}
    acct = payload.get("account") or {}
    scanned = counts.get("total", len(payload.get("candidates") or []))
    if acct.get("handle_unset"):
        acct_html = ('<span class="tag warn">handle unset</span> accounts.%s.handle is '
                     'still a placeholder, so there is nowhere to post from'
                     '<br><span class="empty">Every comment draft below is '
                     'undeliverable until it is filled in.</span>' % channel)
    elif acct.get("present"):
        acct_html = '<span class="tag go">@%s</span>' % e(acct.get("handle") or "?")
    elif channel == "ph":
        acct_html = '<span class="tag warn">no active PH account</span>'
    else:
        acct_html = None   # buzz-scan.py emits no account block; omit the section

    rows = []
    for i, item in enumerate(carded, 1):
        c, d = item["cand"], item["draft"]
        names = ", ".join(m.get("name") or m.get("username") or "?"
                          for m in item["makers"]) or "—"
        if channel != "ph":   # threads have an author, not a maker team
            names = c.get("author") or "—"
        reach = []
        for m in item["makers"]:
            s = m.get("socials") or {}
            if s.get("twitter"):
                reach.append("twitter")
            elif s.get("linkedin"):
                reach.append("linkedin")
        reach = ", ".join(sorted(set(reach))) or "none"
        if channel != "ph":
            # "Reachable via" is a PH outreach concept. For a thread the useful
            # third column is where it lives, since that drives the posting lane.
            reach = ("r/%s" % c["subreddit"]) if c.get("subreddit") else \
                    (c.get("platform") or "").upper()
        rows.append(
            "<tr><td class='rank'>%d</td><td><b>%s</b></td><td>%s</td><td>%s</td>"
            "<td class='num'>%s</td></tr>"
            % (i, e(c_title(c)), e(names), e(reach), e(d.get("relevance", "—"))))

    miss_html = ""
    if missing:
        miss_html = ('<p class="note"><b>%d draft(s) did not match any scanned '
                     'launch</b> and were dropped: %s</p>'
                     % (len(missing), e(", ".join(filter(None, missing)))))

    unaccounted = scanned - (len(carded) + len(upvote) + len(skipped))
    gap = ""
    if unaccounted > 0:
        gap = ('<p class="note"><b>%d scanned launch(es) carry no judgement</b> — '
               'they were not passed to this document, so the day is not fully '
               'accounted for. Pass every scanned launch in the `judged` list.</p>'
               % unaccounted)

    word = ITEM_WORD.get(channel, "item")
    # First thing on the page, above the scan diagnostics: the lane's goal.
    goal_block = checklist_for(channel)
    acct_block = ("<h3>Account</h3>\n<p class=\"facts\">%s</p>" % acct_html
                  if acct_html else "")
    scan_block = "<h3>Scan</h3>\n<p class=\"facts\">%s</p>" % render_scan_facts(
        payload, channel)
    return """<section class="page">
<h1>%s digest</h1>
<p class="sub">%s &nbsp;·&nbsp; %d scanned &nbsp;·&nbsp; %d carded &nbsp;·&nbsp;
%d filtered signals &nbsp;·&nbsp; %d skipped</p>
<div class="rule"></div>
%s%s%s
<h3>Carded %ss, highest value first</h3>
<table><thead><tr><th class="rank">#</th><th>%s</th><th>%s</th>
<th>%s</th><th class="num">Score</th></tr></thead><tbody>%s</tbody></table>
<p class="note">Every action in this document is manual. Nothing here has been
posted, sent, upvoted, or followed.</p>%s%s
</section>""" % (e(CHANNEL_LABEL.get(channel, channel)),
                 e(date), scanned, len(carded), len(upvote), len(skipped),
                 goal_block, scan_block, acct_block, word,
                 "Product" if channel == "ph" else "Thread",
                 "Maker" if channel == "ph" else "Author",
                 "Reachable via" if channel == "ph" else "Where",
                 "".join(rows) or
                 "<tr><td colspan='5' class='empty'>no %ss carded</td></tr>" % word,
                 miss_html, gap)


def plain(s):
    """Normalize typography for readable recommended guidance."""
    for bad, good in (("—", " - "), ("–", "-"),
                      ("‘", "'"), ("’", "'"),
                      ("“", '"'), ("”", '"'),
                      ("…", "..."), (" ", " ")):
        s = (s or "").replace(bad, good)
    return re.sub(r" {2,}", " ", s)


def _window_tag(rw):
    """The reply-window verdict as a tag. `open` is the only one that earns a
    draft, so it is the only one that reads as go."""
    v = (rw or {}).get("verdict") or "unscored"
    cls = {"open": "go", "weak": "hold"}.get(v, "warn")
    score = (rw or {}).get("score")
    label = "%s%s" % (v, "" if score is None else " · window %d/10" % score)
    return '<span class="tag %s">%s</span>' % (cls, e(label))


def render_comment_queue(carded, channel="ph"):
    """Recommended copy is guidance; HN items are research only, including mixed runs."""
    items = [it for it in carded
             if (it["cand"].get("platform") or channel).lower() in ("hn", "reddit")]
    if not items:
        return ""
    blocks = []
    for i, item in enumerate(items, 1):
        c, d = item["cand"], item["draft"]
        platform = (c.get("platform") or channel).lower()
        reasons = "".join("<li>%s</li>" % e(reason)
                          for reason in (c.get("reply_window") or {}).get("reasons", []))
        context = ("<p>Discussion context:</p><ul>%s</ul>" % reasons) if reasons else ""
        if platform == "hn":
            guidance = ("<p>Research only: HN prohibits generated or AI-edited text. "
                        "Read the source and write any contribution yourself. "
                        "Generated comment text is omitted.</p>")
            if d.get("research_notes"):
                guidance += '<div class="draft">%s</div>' % e(d["research_notes"])
        else:
            guidance = ('<p>Recommended copy is guidance. Follow platform and subreddit '
                        'rules and write your own original content; use only experience '
                        'you can truthfully claim.</p><div class="draft">%s</div>'
                        % e(plain(d.get("comment_draft") or "")))
        blocks.append('<div class="queue"><p class="qhd">%d. %s</p><p>%s</p>%s'
                      '<p>Disclose relevant affiliation. Read the thread and top replies '
                      'before deciding whether to contribute.</p>'
                      '%s<p class="qgrounded">grounded in: %s</p></div>'
                      % (i, e(c_title(c)), _link(c_url(c)), guidance, context,
                         e(d.get("grounded_in") or "excerpt only; verify against source")))
    return ('<section class="page"><h2>Discussion guidance</h2>'
            '<p class="sub">Filtered discussions for independent assessment.</p>%s</section>'
            % "".join(blocks))


def render_upvote(upvote, channel="ph"):
    """Filtered signals; accepts the legacy upvote action input."""
    if not upvote:
        return ""
    word = ITEM_WORD.get(channel, "item")
    rows = "".join(
        "<tr><td class='rank'>%d</td><td><b>%s</b><br><span class='empty'>%s</span>"
        "<br>%s</td><td class='num'>%s</td></tr>"
        % (i, e(c_title(it["cand"])), e(c_sub(it["cand"])),
           _link(c_url(it["cand"])), e(it["draft"].get("relevance", "—")))
        for i, it in enumerate(upvote, 1))
    return """<section class="page">
<h2>Filtered signals</h2>
<p class="sub">%d relevant %s(s) to assess independently. These signals help you
focus; they are not requests to vote or comment.</p>
<div class="rule"></div>
<table><thead><tr><th class="rank">#</th><th>%s</th>
<th class="num">Score</th></tr></thead><tbody>%s</tbody></table>
</section>""" % (len(upvote), word, word.capitalize(), rows)


def render_skipped(skipped, channel="ph"):
    """Full-day accounting: what was scanned and dropped, and why. Without this a
    quiet day is indistinguishable from a broken scan."""
    if not skipped:
        return ""
    rows = "".join(
        "<tr><td><b>%s</b></td><td>%s</td><td class='num'>%s</td></tr>"
        % (e(c_title(it["cand"])),
           e(it["draft"].get("reason") or it["draft"].get("why_relevant"))
           or "<span class='empty'>no reason recorded</span>",
           e(it["draft"].get("relevance", "—")))
        for it in skipped)
    return """<section class="page">
<h2>Not carded</h2>
<p class="sub">Scanned, judged, and dropped — %d %s(s). Recorded so a quiet
day can be told apart from a failed scan.</p>
<div class="rule"></div>
<table><thead><tr><th>%s</th><th>Why it was dropped</th>
<th class="num">Score</th></tr></thead><tbody>%s</tbody></table>
</section>""" % (len(skipped), ITEM_WORD.get(channel, "item"),
                 "Product" if channel == "ph" else "Thread", rows)


def render_launch(item, idx, channel="ph"):
    c, d, makers = item["cand"], item["draft"], item["makers"]
    facts = c_facts(c)

    maker_blocks = []
    for m in makers:
        s = m.get("socials") or {}
        links = []
        if s.get("twitter"):
            src = s.get("twitter_source") or "unknown source"
            handle = s["twitter"].rsplit("/", 1)[-1]
            links.append("Twitter %s <span class='tag neutral'>%s</span>"
                         % (_link(s["twitter"], "@" + handle), e(src)))
        if s.get("linkedin"):
            links.append("LinkedIn %s <span class='tag neutral'>%s</span>"
                         % (_link(s["linkedin"], "profile"),
                            e(s.get("linkedin_source") or "unknown source")))
        if not links:
            links.append({
                "identity_redacted": "<span class='empty'>Product Hunt withholds this "
                    "maker's identity.</span>",
                "no_handle_on_ph": "<span class='empty'>No handle returned by the PH API. "
                    "Check the maker's public profile for self-declared links.</span>",
            }.get(m.get("reachability"),
                  "<span class='empty'>No Twitter or LinkedIn found via the API.</span>"))
        if m.get("notes"):
            links.append("<span class='tag warn'>%s</span>" % e(m["notes"]))
        reply = ""
        if m.get("reply_draft"):
            which = (m.get("outreach") or "twitter").lower()
            label = ("Suggested reply guidance — write your own original content"
                     if which == "twitter" else "Suggested connection guidance — write your own original content")
            reply = ('<div class="draft"><span class="who">%s</span>%s</div>'
                     % (e(label), e(m["reply_draft"])))
        maker_blocks.append(
            '<div class="maker"><div class="name">%s</div><p class="links">%s</p></div>%s'
            % (e(m.get("name") or m.get("username") or "unknown maker"),
               " &nbsp;·&nbsp; ".join(links), reply))

    comment = ""
    platform = (c.get("platform") or channel).lower()
    if platform == "hn":
        comment = ("<p>Research only: HN prohibits generated or AI-edited text. "
                   "Write any contribution yourself and disclose relevant affiliation.</p>")
        if d.get("research_notes"):
            comment += '<div class="draft">%s</div>' % e(d["research_notes"])
    elif d.get("comment_draft") and platform == "reddit":
        comment = "<p>Recommended copy appears in Discussion guidance.</p>"
    elif d.get("comment_draft"):
        grounded = d.get("grounded_in") or "launch description only; verify against source"
        comment = ('<div class="draft"><span class="who">Recommended copy — guidance</span>'
                   '%s<div class="grounded">grounded in: %s</div></div>'
                   '<p>Follow platform rules and write your own original content. '
                   'Disclose relevant affiliation; use only experience you can truthfully claim.</p>'
                   % (e(d["comment_draft"]), e(grounded)))
    people = ("<h3>Maker context</h3>\n%s" %
              ("".join(maker_blocks) or "<p class='empty'>no makers found</p>")) if platform == "ph" else ""
    return """<section class="page">
<span class="score">%s/10</span>
<h2>%s</h2>
<p class="sub">%s</p>
<div class="rule"></div>
<p class="facts">%s</p>
<p class="facts">%s</p>
<h3>Why it is relevant</h3>
<p>%s</p>
%s
%s
</section>""" % (
        e(d.get("relevance", "—")), e(c_title(c)), e(c_sub(c)),
        " &nbsp;·&nbsp; ".join(facts) or "&nbsp;",
        _link(c_url(c)),
        e(d.get("why_relevant")) or "<span class='empty'>not stated</span>",
        comment, people)


def render_html(payload, date, carded, upvote, skipped, missing,
                channel="ph"):
    body = [render_cover(payload, date, carded, upvote, skipped, missing,
                         channel)]
    # Straight after the cover, before the per-item reading pages: this is the
    # page someone acts from, so it must not be reachable only by scrolling past
    # everything else.
    body.append(render_comment_queue(carded, channel))
    body += [render_launch(it, i, channel) for i, it in enumerate(carded, 1)]
    body.append(render_upvote(upvote, channel))
    body.append(render_skipped(skipped, channel))
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<title>%s digest %s</title><style>%s</style></head><body>%s</body></html>"
            % (e(CHANNEL_LABEL.get(channel, channel)), e(date), CSS, "".join(body)))


# ----------------------------- PDF conversion ---------------------------------

def to_pdf(html_path, pdf_path):
    """playwright → headless Chrome → give up loudly. Returns the tool used."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(chromium_sandbox=True)
            pg = b.new_page()
            pg.goto("file://%s" % html_path, wait_until="load")
            pg.pdf(path=pdf_path, format="A4", print_background=True)
            b.close()
        return "playwright"
    except Exception as exc:
        print("  playwright unavailable (%s) — trying headless Chrome"
              % str(exc).splitlines()[0][:80], file=sys.stderr)
    if os.path.exists(pdf_path):
        os.remove(pdf_path)  # so a stale file can't be mistaken for a fresh render
    for c in CHROME_CANDIDATES:
        exe = c if os.path.exists(c) else shutil.which(c)
        if not exe:
            continue
        err = ""
        with tempfile.TemporaryDirectory() as prof:
            try:
                # Chrome writes the PDF in ~2s and then, with an isolated
                # --user-data-dir, never exits. So don't wait on exit: poll until
                # the file exists and its size stops changing, then kill it. The
                # isolated profile is kept deliberately — printing must not touch
                # the user's real Chrome profile or a running instance.
                p = subprocess.Popen(
                    [exe, "--headless", "--disable-gpu",
                     "--no-pdf-header-footer", "--user-data-dir=%s" % prof,
                     "--no-first-run", "--no-default-browser-check",
                     "--disable-extensions", "--disable-sync",
                     "--disable-default-apps", "--disable-component-update",
                     "--print-to-pdf=%s" % pdf_path, "file://%s" % html_path],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                last, stable, waited = -1, 0, 0.0
                while waited < 60:
                    if p.poll() is not None:  # exited on its own
                        break
                    size = os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0
                    if size > 1000 and size == last:
                        stable += 1
                        if stable >= 2:  # two identical samples = write finished
                            break
                    else:
                        stable = 0
                    last = size
                    time.sleep(0.4)
                    waited += 0.4
                else:
                    err = "no PDF after 60s"
                if p.poll() is None:
                    p.kill()
                    p.wait(timeout=10)
                if p.stderr and not err:
                    err = (p.stderr.read() or "").strip()
            except OSError as exc:
                err = str(exc)
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
            return os.path.basename(exe)
        print("  %s failed: %s" % (os.path.basename(exe), err[:200] or "no PDF written"),
              file=sys.stderr)
    return None


# ----------------------------- main -------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", help="scanner JSON (default: newest in buzz/working/)")
    ap.add_argument("--drafts", required=True, help="JSON list of carded launches")
    ap.add_argument("--channel", choices=["ph", "hn", "reddit", "buzz"],
                    help="override channel detection (default: inferred from the "
                         "candidates' `platform` field)")
    ap.add_argument("--out", help="output PDF (default: "
                                  "buzz/working/<channel>-digest-DD-MM-YYYY.pdf)")
    ap.add_argument("--html-only", action="store_true", help="skip PDF conversion")
    args = ap.parse_args()

    cpath = args.candidates or newest_candidates()
    with open(cpath) as f:
        payload = json.load(f)
    if isinstance(payload, list):  # tolerate a bare candidates list
        payload = {"candidates": payload}
    with open(args.drafts) as f:
        judged = read_judged(json.load(f))
    for d in judged:
        if not d.get("ph_link"):
            sys.exit("every --drafts entry needs ph_link (the merge key)")

    date = digest_date(payload, cpath)
    channel = detect_channel(payload, args.channel)
    carded, upvote, skipped, missing = merge(
        payload.get("candidates") or [], judged)

    out_pdf = args.out or os.path.join(output_dir(),
                                       out_name(channel, date))
    # Absolute, always: the renderer loads the HTML as a file:// URL, and a relative
    # path there ("file://buzz/working/x.html") is an invalid URL to Chromium.
    out_pdf = os.path.abspath(out_pdf)
    os.makedirs(os.path.dirname(out_pdf), exist_ok=True)
    out_html = os.path.splitext(out_pdf)[0] + ".html"
    with open(out_html, "w") as f:
        f.write(render_html(payload, date, carded, upvote, skipped,
                            missing, channel))

    print("%s digest %s — %d carded, %d filtered signals, %d skipped"
          % (channel, date, len(carded), len(upvote), len(skipped)))
    if missing:
        print("  ⚠ %d draft(s) matched no scanned launch (dropped): %s"
              % (len(missing), ", ".join(filter(None, missing))))

    if args.html_only:
        print("HTML → %s" % show(out_html))
        return
    tool = to_pdf(out_html, out_pdf)
    if tool:
        os.remove(out_html)
        print("PDF (%s) → %s  [%.0f KB]"
              % (tool, show(out_pdf),
                 os.path.getsize(out_pdf) / 1024.0))
    else:
        sys.exit("PDF conversion failed — no playwright and no Chrome/Chromium.\n"
                 "The rendered HTML is intact at %s (open it, or print to PDF "
                 "from a browser)." % show(out_html))


if __name__ == "__main__":
    main()
