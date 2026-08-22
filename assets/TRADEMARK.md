# Trademark notice

The "OmniRogue" name and the OmniRogue logo (`omnirogue-logo.png` in this
directory) are trademarks of OmniRogue Inc. They are **not** licensed under
this project's MIT license — see `LICENSE` for the full carve-out.

You may use OmniAgentOS Starter's code freely under MIT, including running it
under your own brand. To do that, do not use the OmniRogue name or logo as
your own branding. Instead, set:

```
OMNIAGENTOS_BRAND_NAME=<your brand name>
OMNIAGENTOS_BRAND_LOGO=<a URL, or /assets/<file> after copying the file into assets/>
```

`OMNIAGENTOS_BRAND_LOGO` goes straight into the dashboard's `<img src>`, so it
must be something the browser can fetch — a bare filesystem path will 404.
See the README's White-label section for the two forms that actually work.
Set these **before** starting the server (`./start.sh` / `start.ps1`); brand
is resolved once at startup, so a browser refresh alone will not pick up a
change, only a restart will.

Questions about using the OmniRogue name or logo itself — e.g. in a fork's
own README or marketing — go to OmniRogue Inc, not this repository's issue
tracker.
