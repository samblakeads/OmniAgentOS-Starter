# Trademark notice

The "OmniRogue" name and the OmniRogue logo (`omnirogue-logo.png` in this
directory) are trademarks of OmniRogue Inc. They are **not** licensed under
this project's MIT license — see `LICENSE` for the full carve-out.

You may use OmniAgentOS Starter's code freely under MIT, including running it
under your own brand. To do that, do not use the OmniRogue name or logo as
your own branding. Instead, set:

```
OMNIAGENTOS_BRAND_NAME=<your brand name>
OMNIAGENTOS_BRAND_LOGO=<path to your own logo>
```

before starting the server, and the dashboard header (and `GET /api/health`
`brand` field) will use your values instead of the OmniRogue defaults.

Questions about using the OmniRogue name or logo itself — e.g. in a fork's
own README or marketing — go to OmniRogue Inc, not this repository's issue
tracker.
