# Visual walkthrough

Four scenes explain relevant community threads, a useful reply draft, daily platform digests
with a separate PDF briefing for each in the configured Slack channel, and a person
following platform rules and writing original content. The delivery scene animates the three platform rows.

All threads, reply text and digest layouts are fictional illustrations. No scan,
model session, Slack post or platform action was performed. Live drafts must be
grounded in their source thread. Slack/PDF delivery still needs the configured host
and credentials. The workflow never posts, comments or upvotes on a community platform;
a person decides independently whether to contribute. HN cards provide research
only; generated and AI-edited HN text must not be posted.

Use Python 3.12 and install the optional `.github/assets/requirements.txt` into a
temporary environment. From the repo root:

```sh
python .github/assets/render-visual-demo.py --preview-dir /tmp/community-demo
```

The renderer draws the motion from `story.json`; it makes no network call and uses no
token, scanner or account. No fonts or third-party artwork are bundled. Arial or
DejaVu Sans supplies the type. Pillow is an optional capture dependency under MIT-CMU
terms, separate from the plugin runtime. Inspect all four keyframes and the animation.

GIF encoding may merge unchanged frames into a longer hold. Verify all four scenes by elapsed playback time, not by equally spaced frame indices.
