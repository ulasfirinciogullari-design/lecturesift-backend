# Retired YouTube URL input

YouTube and all other URL-based source imports were deliberately removed from
the product. The supported source flow is file upload for video, audio and
documents. The workspace must not show a URL field or describe YouTube links as
supported.

`POST /jobs/url` remains only as a compatibility tombstone for stale clients.
It is excluded from OpenAPI and returns HTTP 410 with `LS-URL-06` before
authentication, billing, quota reservation, job creation or network access.
The retired downloader entry point fails with the same code. Already completed
historical results remain available, while queued legacy URL jobs terminate
without starting media work.

The former yt-dlp, Deno/EJS, playback-client and proof-of-origin-token
experiments are historical investigation, not a supported capability. Repeated
isolated checks still encountered provider bot challenges from cloud networks;
none established a dependable production download path. Do not restore those
components, add account cookies, proxies or third-party download services, or
reactivate the hidden endpoint based on the old implementation.

`tests/test_remote_download.py` protects the retirement contract: YouTube,
direct-media and arbitrary URLs cannot use the network, create files, reserve a
balance or enqueue work. Future changes must preserve completed historical
artifacts.

Re-enabling URL imports requires a new explicit product decision and a separate
release design with a lawful, provider-supported integration, bounded cost and
resource behavior, credential isolation, recovery behavior and remote
integration evidence. Until those conditions exist, YouTube support must remain
disabled.
