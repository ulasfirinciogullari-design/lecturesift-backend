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

This revision installs the pinned Deno `2.9.5` Linux x86_64 wheel and explicitly
selects it in yt-dlp. The extractor is pinned to `2026.8.19` and EJS to its
required `0.8.0` version. Both additions have no Python dependencies. Their
artifact hashes were read from PyPI release metadata and appended to the
existing lock without upgrading other packages. Only the prebuilt Linux wheel
is allowed for Deno; no source build can fetch an unpinned runtime. The lock
platform floor is manylinux 2.27 (supported by the existing Debian image).

The input and lock fingerprints were updated. EJS scripts are installed at
build time; runtime component downloads are disabled. The image capability
check executes Deno and imports both Python components. CI builds the
application image to run that check.

YouTube goes directly through its maintained extractor. Generic page scraping
and direct-media URL handling were removed from this input. Empty or partial
files are not successful downloads; the final merged file also respects the
configured size limit.

If the default clients encounter a bot challenge, unavailable format or HTTP
403, the downloader makes one fallback attempt with yt-dlp's supported
`web_safari` and `web_embedded` public playback clients. It removes only its own
partial output before changing formats. HTTP 429, age/account requirements and
private-video errors are not retried. This is a bounded compatibility fallback,
not a guarantee that the provider accepts a server IP.

## Validation limits

Regression cases use synthetic downloader results, never user videos or
credentials. They cover accepted/rejected URLs, browser/server agreement,
rejection before job/plan work, solver configuration, partial files, final
size limits and provider-block errors. The shared image is checked in remote
CI, not built on the shared development/production VPS.

The reported failure was `LS-URL-02`; the owner clarified that YouTube downloading
fails generally and asked not to narrow this to a supplied example. That
code groups provider rate limits and sign-in/bot responses. Missing runtime
components are a confirmed source/image deficiency, not proof of the cause of
that individual failure. A successful synthetic test or image build does not
prove that YouTube accepts the production IP or that every video is available.
CI additionally probes the seven-second public sample `x41yOUIvK2k` from the
pinned yt-dlp extractor tests. It uses the actual application downloader and
checks for a readable audio stream in an isolated disposable container. A
120-second deadline, memory/CPU/process limits, small temporary filesystems and
cleanup bound the probe. Only its result, error code, size and duration are
printed; no video or provider diagnostics are retained. The probe is reported
separately from deterministic tests because provider availability can change.
It does not verify the production IP. No production deployment, account cookies,
proxy purchase or real payment was performed as part of this change.

The `933b367` remote run passed 1,198 deterministic regression tests (3 skips),
including isolated PostgreSQL concurrency checks, and the application image
check. The browser run had two mobile assistant failures caused by the cookie
banner covering its launch button; that fix is pending its next remote run.

The real YouTube probe still returned `LS-URL-02` / `bot_challenge`. It now runs
with the pinned bgutil HTTP PO-token provider (`2.0.0`) in a private container
sharing the downloader network. Diagnostic booleans confirmed `provider_seen`
and `token_generated`, with no `provider_error`. A generated token therefore
has not resolved the cloud runner's challenge. No token, cookie, or raw provider
response is logged. This external failure must not be hidden by the green
aggregate workflow status.

`YOUTUBE_POT_BASE_URL` is optional and limited to the reviewed private endpoints
`http://127.0.0.1:4416` and `http://youtube-pot:4416`. Setting it selects the
supported mweb client and requests fresh PO tokens; it never exposes the
provider publicly or enables arbitrary proxy destinations. The private provider
has only been configured in isolated CI, not production. The maintained provider
itself warns that PO tokens cannot guarantee removal of IP/bot challenges:
https://github.com/Brainicism/bgutil-ytdlp-pot-provider
