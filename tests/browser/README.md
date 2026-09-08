# Remote browser smoke checks

The `browser-smoke` job in `.github/workflows/test.yml` uses a disposable
GitHub-hosted Ubuntu runner, not the user's computer or the production OVH host.
It needs no repository secrets, real account, payment, API key, or database.
Main pushes and pull requests are covered; feature pushes without a PR are not
duplicated. `workflow_dispatch` allows an explicit run. A newer revision cancels
the older run for the same PR/ref.

CI runs these preparation commands (do not duplicate them locally on a constrained PC):

```sh
cd tests/browser
npm ci --ignore-scripts --no-audit --no-fund
cd ../..
node scripts/build_localized_site.mjs
cd tests/browser
npx --no-install playwright install --with-deps chromium
```

The three-package Playwright dependency graph is version-pinned in `package.json`
and `package-lock.json`, including SHA-512 integrity values read from the official
npm registry. CI uses `npm ci`; install scripts are disabled. Actions follow the existing
repository major-version convention. Dependencies/browser downloads require
network access in the preparation steps. The actual test command is then run
inside a fresh Linux network namespace with only loopback enabled:

```sh
node tests/browser/node_modules/@playwright/test/cli.js test --config tests/browser/playwright.config.mjs
```

See the workflow for the `sudo unshare --net` wrapper and the shared browser-cache
path. Root creates the namespace, then `setpriv` switches to the runner's UID/GID
before Node, the server, or Chromium starts. CI and the explicit browser-cache
environment are preserved, with HOME set to the runner's home. There is no fallback
to an unrestricted run if isolation fails. The configuration rejects root execution
and any non-loopback network interface in CI.
Browser routing additionally fulfills only an explicit synthetic GET API contract,
blocks all other remote requests (including fonts, ads and payment providers),
fails unexpected origins, API queries or WebSockets, and blocks service workers.
The reviewed pages use no external font downloads; the tolerated third-party
origin allowlist is empty. The server
serves only built `dist/` assets on `127.0.0.1:4173`, including canonical routes.

One Chromium worker runs desktop/mobile in light/dark mode: English home menu,
theme switch, wrong/correct/reset sample quiz, canonical localized navigation,
Arabic RTL sample keyboard navigation, and the real workspace's requested-job
restore/result/quiz scoring/tab/reset flow using a fabricated account and job.
Assertions include horizontal overflow, missing assets and uncaught page errors.
This is not a screenshot-baseline, live payment, provider, or full accessibility
audit; no customer documents or paid analysis are performed.

Only failures upload synthetic screenshots/traces/reports, retained for three
days. Reports from a public repository may be public, so never add real tokens,
addresses, documents, or production responses to fixtures or test artifacts.
Do not change this to `pull_request_target`, add deployment secrets, or use a
production self-hosted runner. Initial validation must happen in remote CI;
these checks were added without starting a local test/build/browser/server.

Primary references: [Playwright CI](https://playwright.dev/docs/ci-intro),
[network interception](https://playwright.dev/docs/network),
[isolated browser contexts](https://playwright.dev/docs/api/class-browsercontext).
