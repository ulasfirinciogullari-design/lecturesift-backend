# LectureSift development

## Local resource budget

The owner uses this Windows computer for other work and has reported repeated
slowdowns during tests. Keep local activity to small source edits and bounded
read-only inspection. Do not run pytest suites, local builds, Docker, OCR,
FFmpeg or browser test matrices here unless the owner explicitly opts in.

Run automated checks through the existing GitHub Actions workflow. Use a
separately provisioned remote test environment for browser/media integration
tests; a production VPS is not automatically an authorised test runner.
Do not buy a new service or move production data just to run checks.

Keep at most one task-owned preview tab when a visual check is necessary,
and close task-owned tabs and servers afterward. Do not close the owner's or
another task's tabs or kill unrelated processes. Avoid parallel local test
processes. Never imply that remote execution has been configured until it has
actually been verified.

## Release truthfulness

Distinguish source changes, preview publication, passing CI and live activation.
Do not treat a health response as proof of real payments, ad revenue or all
media formats working. Do not activate referral rewards while their versioned
schema/recovery release capability is false. Never log or commit secrets.

## Authenticated browser access

Before promising to inspect a signed-in provider panel, verify that this task
has a callable browser read/control tool and can read the intended page.
An ambient tab URL or an open preview is not proof of page access. If enabling
Browser does not expose those tools, report that observed limitation and
distinguish it from an unverified root cause. Do not repeatedly ask the owner
to enable Browser or type the same mention. Never copy browser cookies or
credentials into a separate browser to work around a missing connection.
