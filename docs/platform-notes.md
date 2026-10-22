# Platform notes

## Rules and access

- [Hacker News guidelines](https://news.ycombinator.com/newsguidelines.html) prohibit
  generated and AI-edited comments and vote solicitation. Buzz provides HN research
  guidance only. Write any contribution independently.
- [Product Hunt API docs](https://api.producthunt.com/v2/docs) restrict commercial use.
  Obtain permission from Product Hunt before business use. API access may be rate-limited.
- [Reddit Data API guidance](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)
  describes access requirements. Public RSS does not establish permission for every use.
  Supply your own honest `reddit.user_agent`; do not impersonate a browser or evade a block.

## Missing context

Buzz never automatically fetches PH maker profiles or websites. Maker social
links may be absent; a missing link is not a failure to resolve the identity. Buzz does
not collect a voter or commenter supporter pool. Discussion text can still supply context.
The PH API may redact identities. Do not infer or reconstruct withheld identities.

RSS can omit scores and comment counts. Treat them as unknown. Report missing feeds and
rate limits rather than presenting a partial scan as complete. Retry 429 responses only
within the configured budget; blocked requests are not retried with a different identity.

## Slack uploads

Slack's external upload flow is used for PDFs. A successful completion response is the
acknowledgment; indexing can lag. Removing an uploaded file does not necessarily remove
the message that referenced it. No platform action is performed by Buzz.
