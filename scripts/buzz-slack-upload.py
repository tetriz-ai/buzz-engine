#!/usr/bin/env python3
"""
buzz-slack-upload.py — share a file (normally a /buzz digest PDF) into Slack.

Kept separate from buzz-digest-pdf.py on purpose: the renderer is deliberately
network-free and dependency-free so it can run anywhere, and uploading is the one
thing that needs a credential and the internet. This also makes the uploader
reusable for any channel's digest, not just PH.

Slack retired `files.upload`, so this uses the three-step external flow:
  1. files.getUploadURLExternal  -> a one-time presigned URL + file_id
  2. POST the bytes to that URL
  3. files.completeUploadExternal -> registers the file and (optionally) shares it

Token: SLACK_BOT_TOKEN in buzz/.env (gitignored) or the environment.
Scopes needed: files:write to upload, plus chat:write for the initial comment.
The bot must be a member of the target channel or step 3 returns not_in_channel.

Usage:
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-slack-upload.py --file buzz/working/ph-digest-06-08-2026.pdf \
      --channel C0123456789 --comment "Today's PH digest"
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/buzz-slack-upload.py --file <path> --dry-run   # prove it without posting
"""

import argparse
import json
import mimetypes
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

from buzzpaths import REPO, ENV_PATH, CONFIG_PATH
SLACK_API = "https://slack.com/api/%s"
# Slack's own ceiling is 1GB, but a digest that big means something is wrong.
MAX_BYTES = 50 * 1024 * 1024


def load_env(path):
    if not os.path.exists(path):
        return {}
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def api(method, token, params):
    """POST a form-encoded Slack API call. Raises on ok:false so no step can fail
    silently — the original shell version would happily POST a file to the string
    "null" when step 1 errored."""
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(SLACK_API % method, data=data, method="POST")
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.loads(r.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError("%s failed: %s" % (method, body.get("error") or body))
    return body


def put_bytes(url, path, filename):
    """Step 2: multipart POST of the raw file to the presigned URL. This endpoint
    answers with a bare HTTP status, not JSON."""
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    boundary = "----buzz%s" % uuid.uuid4().hex
    with open(path, "rb") as f:
        payload = f.read()
    body = b"".join([
        ("--%s\r\n" % boundary).encode(),
        ('Content-Disposition: form-data; name="file"; filename="%s"\r\n' % filename).encode(),
        ("Content-Type: %s\r\n\r\n" % ctype).encode(),
        payload, b"\r\n", ("--%s--\r\n" % boundary).encode(),
    ])
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=%s" % boundary)
    with urllib.request.urlopen(req, timeout=180) as r:
        if r.status != 200:
            raise RuntimeError("upload POST returned HTTP %s" % r.status)
    return len(payload)


def get_token():
    token = load_env(ENV_PATH).get("SLACK_BOT_TOKEN") or os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        sys.exit("Missing SLACK_BOT_TOKEN.\n"
                 "  → add `SLACK_BOT_TOKEN=xoxb-...` to buzz/.env (gitignored),\n"
                 "    or export it (container runs).\n"
                 "  → the app needs files:write (upload) and chat:write (comment),\n"
                 "    and must be invited to the target channel.")
    return token


def upload_config():
    import yaml
    with open(CONFIG_PATH) as handle:
        return yaml.safe_load(handle) or {}


def destination(channel, config):
    configured = (config.get("slack") or {}).get("channel_id")
    if not configured or str(configured).startswith("FILL_IN_"):
        raise ValueError("Set slack.channel_id before uploading")
    if channel is not None and channel != configured:
        raise ValueError("Destination must match configured slack.channel_id")
    return configured


def report_path(value, config):
    root = Path(REPO).absolute()
    resolved_root = root.resolve()
    output = root / config.get("output_dir", "buzz/working")
    resolved_output = output.resolve()
    # Configuration can narrow the report tree, never escape the project.
    if resolved_output == resolved_root or resolved_root not in resolved_output.parents:
        raise ValueError("Expected a report in the configured output directory")
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    if (resolved_output not in resolved.parents or not resolved.is_file()
            or path.is_symlink() or resolved.stat().st_nlink != 1):
        raise ValueError("Expected a regular report in the configured output directory")
    # Reject links in parent directories as well as in the leaf.
    current = path
    while current != root and current != current.parent:
        if current.is_symlink():
            raise ValueError("Expected a regular report in the configured output directory")
        current = current.parent
    return str(resolved)


def post_text(args):
    config = upload_config()
    text = args.text
    if text.startswith("@"):
        p = report_path(text[1:], config)
        with open(p) as f:
            text = f.read()
    if not text.strip():
        sys.exit("refusing to post an empty message")
    token = get_token()
    args.channel = destination(args.channel, config)
    who = api("auth.test", token, {})
    print("slack: %s as %s (%s)" % (who.get("team"), who.get("user"), who.get("team_id")))
    if args.dry_run:
        print("dry run — token verified, %d chars not posted" % len(text))
        return
    done = api("chat.postMessage", token, {"channel": args.channel, "text": text,
                                           "unfurl_links": "false"})
    print("posted message ts=%s to %s" % (done.get("ts"), args.channel))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="path to the file to share")
    ap.add_argument("--text", help="post a plain text message instead of a file "
                    "(needs --channel and chat:write). Use @path to read it from a file.")
    ap.add_argument("--channel", help="Slack channel id, e.g. C0123456789. Omit to "
                                      "use the configured slack.channel_id.")
    ap.add_argument("--comment", help="initial_comment posted with the file")
    ap.add_argument("--title", help="file title in Slack (default: the filename)")
    ap.add_argument("--dry-run", action="store_true",
                    help="check token identity and local input, then stop; does not prove scopes or channel membership")
    args = ap.parse_args()
    if bool(args.file) == bool(args.text):
        sys.exit("pass exactly one of --file or --text")
    if args.text:
        return post_text(args)

    config = upload_config()
    path = report_path(args.file, config)
    if not os.path.exists(path):
        sys.exit("no such file: %s" % args.file)
    # os.path.getsize, not `wc -c`: on macOS wc pads its output with spaces and
    # $(...) does not strip them, so `length` arrives malformed and Slack answers
    # invalid_arguments — a failure that looks nothing like its cause.
    size = os.path.getsize(path)
    if size == 0:
        sys.exit("refusing to upload an empty file: %s" % args.file)
    if size > MAX_BYTES:
        sys.exit("file is %.1f MB, over the %d MB sanity limit"
                 % (size / 1e6, MAX_BYTES // (1024 * 1024)))
    name = os.path.basename(path)

    token = get_token()
    args.channel = destination(args.channel, config)

    who = api("auth.test", token, {})
    print("slack: %s as %s (%s)" % (who.get("team"), who.get("user"), who.get("team_id")))
    print("file : %s  (%s bytes)" % (name, size))

    if args.dry_run:
        print("dry run — token and file verified, nothing uploaded or posted")
        return

    up = api("files.getUploadURLExternal", token, {"filename": name, "length": size})
    sent = put_bytes(up["upload_url"], path, name)
    files = [{"id": up["file_id"], "title": args.title or name}]
    params = {"files": json.dumps(files)}
    if args.channel:
        params["channel_id"] = args.channel
    if args.comment:
        params["initial_comment"] = args.comment
    done = api("files.completeUploadExternal", token, params)

    info = (done.get("files") or [{}])[0]
    print("uploaded %d bytes → %s" % (sent, info.get("permalink") or up["file_id"]))
    if args.channel:
        # completeUploadExternal returns an empty `channels` even on success, so
        # its absence is NOT a failure signal. ok:true is the real one.
        print("shared to %s (the API's `channels` field is empty even when the "
              "share succeeds — verify in Slack, not from this response)" % args.channel)
    else:
        print("not shared to any channel (no --channel given)")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        sys.exit(str(error))
