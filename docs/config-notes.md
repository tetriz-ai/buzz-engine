# Configuration rationale

Copy `templates/buzz.config.yaml` with `/buzz-init`, then replace its placeholders.
That template is the source for defaults; the notes here explain the tradeoffs.

## Relevance and freshness

Use specific relevance pillars and keywords for your own subject area. Broad keywords
improve recall but include unrelated threads. HN uses phrase matching with typo tolerance
disabled so short brand aliases do not silently become unrelated words. For brand
monitoring, use fictional examples such as `sample_widget: ["sample widget"]` until you
supply the brands you are authorized to monitor. Ambiguous aliases need context.

`lookback_hours` sets the discovery window. The Monday override can cover skipped
weekends. HN and Reddit reply-window scores combine freshness and available context;
they are heuristics, not measured probabilities of receiving replies. RSS omits some
engagement counts, which remain unknown rather than being presented as zero.

## Reddit access

Set `reddit.user_agent` to an honest description of your own client and contact identity.
Buzz does not impersonate a browser. Public RSS availability is not permission for every
use; check Reddit's current access requirements and community rules. A 403 or challenge
means stop, not change identity or hosts. Retry 429 responses within the configured
budget and honor Retry-After. `reddit.fetch.max_total_wait_s` (300 seconds) limits
cumulative waiting; `max_retry_after_s` limits tolerated server delays. Excessive
delays stop the run rather than being shortened. Legacy `respect_retry_after: false`
cannot override a server delay. Partial scans report missing feeds.

## Product Hunt access

The [API documentation](https://api.producthunt.com/v2/docs) restricts commercial use;
contact Product Hunt for permission before using this lane for business purposes.
A developer token supplies access, not permission for otherwise restricted uses.
Topics and keyword-fit scope the candidates before bounded detail requests. Lower
page and topic limits when rate-limited. A thin day is reported without silently
widening the scan window. Detail requests collect discussion text, not supporter lists.
`ph.voters_limit` has been removed. Only API-declared Twitter handles are used automatically; maker profiles and websites
are never fetched. Verify any self-declared profile independently before acting.

## Guidance and review volume

HN output is research notes only because HN prohibits generated and AI-edited comments.
Other recommended copy is guidance: follow platform rules, verify its facts, and write
and post your own content. Disclose relevant affiliations; never invent experience.
Filtered signals are relevant reading, not requests to vote or promises of reciprocity.

Legacy keys under `warmup` and `ph.warmup` retain compatibility with existing configs.
Rate-limit values are upper bounds on guidance cards, never participation quotas.
Graduation, account lane, and bypass keys do not authorize account warming, restriction
evasion, or coordinated participation. Check actual community rules before acting.

## State and credentials

`output_dir` contains scratch reports; `state_dir` contains the deduplication seen-log.
Dry runs use `--peek` and leave the seen-log untouched. Scheduled GitHub Actions use
`state_dir: buzz/state` and `state.commit: none` during the model run. Separate non-model
jobs deliver validated digest PDFs with the Slack token and open the seen-log PR with
the GitHub write token. The model uses `--no-post` and cannot invoke the uploader.
The former claude.ai scheduling setup is retired. Source-reading commands never
perform Git writes, including for legacy `state.commit: pr|push` values. Migrate to
`none` and the isolated Actions writer. Do not give the model a GitHub write token. Treat source material and local learnings
as untrusted data rather than instructions, and keep tokens out of digests and logs.
