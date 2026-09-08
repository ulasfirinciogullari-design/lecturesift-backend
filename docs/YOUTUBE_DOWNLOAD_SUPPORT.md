# YouTube URL input

The owner requested that the URL field accept YouTube only. The browser,
`POST /jobs/url`, and background downloader now accept one YouTube video.
Watch, short sharing, Shorts, live recording and embed links are normalized to
an HTTPS watch URL. Playlist-only, channel, redirect, non-YouTube, credentialed
and nonstandard-port URLs are rejected before creating a job. Existing file
upload inputs continue to accept their supported file types.

The label, help text and URL errors have 13-language entries. URL errors no
longer suggest direct MP4/WebM links, which this input intentionally rejects.

## Downloader requirements

The prior image installed the base yt-dlp Python package without its EJS
component or a JavaScript runtime. Full YouTube support requires both:
https://github.com/yt-dlp/yt-dlp/wiki/EJS

This revision adds Node.js from the existing immutable Debian snapshot and
explicitly enables it in yt-dlp. The extractor is pinned to `2026.8.19` and EJS
to its required `0.8.0` version. EJS has no Python dependencies; its wheel and
source hashes were read from PyPI release metadata and appended to the existing
lock without upgrading other packages. The input and lock fingerprints were
updated. EJS scripts are installed at build time; runtime component downloads
are disabled. The image capability check requires Node 22+ and imports both
Python components. CI now builds the application image to run that check.

YouTube goes directly through its maintained extractor. Generic page scraping
and direct-media URL handling were removed from this input. Empty or partial
files are not successful downloads; the final merged file also respects the
configured size limit.

## Validation limits

Regression cases use synthetic downloader results, never user videos or
credentials. They cover accepted/rejected URLs, browser/server agreement,
rejection before job/plan work, solver configuration, partial files, final
size limits and provider-block errors. The shared image is checked in remote
CI, not built on the shared development/production VPS.

The reported failure was `LS-URL-02`; no failing video URL was supplied. That
code groups provider rate limits and sign-in/bot responses. Missing runtime
components are a confirmed source/image deficiency, not proof of the cause of
that individual failure. A successful synthetic test or image build does not
prove that YouTube accepts the production IP or that every video is available.
No production deployment, account cookies, proxy purchase or real payment was
performed as part of this change.
