# Low-load development and verification

## Execution split

- Windows: conversation, small source edits and bounded read-only inspection.
  No local pytest suites, builds, Docker, OCR, FFmpeg or browser matrices.
- GitHub-hosted standard Ubuntu runners: backend/OCR/FFmpeg unit and integration
  tests, localized build and isolated synthetic Chromium smoke tests.
- Existing OVH host: a separate `lecturesift-dev` Linux account and checkout at
  `/home/lecturesift-dev/code/lecturesift-backend`. This is not `/opt/lecturesift`,
  not a production deployment and not a self-hosted public-PR runner.

No new server subscription was purchased for this setup. Public repositories
using standard GitHub-hosted runners have free execution minutes; storage and
other account usage remain subject to the account's limits. Failure-only browser
artifacts expire after three days. No claim is made that AI API usage or existing
OVH/Render/Netlify subscriptions are free.

## OVH development account

Verified on 2026-09-08: existing host has 4 vCPU and approximately 8 GB RAM.
The development user has no sudo membership or Docker socket access. Its
`user-1001.slice` is limited to CPUQuota=150%, MemoryHigh=2500M,
MemoryMax=3G and TasksMax=256. An SSH session was observed in that slice.
These are resource caps, not a container/security boundary or a disk quota.
Run untrusted PRs and heavy concurrent tests only on ephemeral GitHub runners.

`deploy/dev-host/80-lecturesift-development.conf` adds only this user to SSH's
allow list, retaining the existing administrator. Password and keyboard-
interactive authentication are disabled for it. Agent/X11/tunnel forwarding is
disabled; local forwards are restricted to loopback ports 1455 (Codex OAuth
callback) and 8765 (explicit preview). Never expose the Codex app server publicly.

A dedicated private key remains on Windows in the ignored `.local-secrets`
directory, protected by a user-only Windows ACL. Only its public key was copied
to the server. The host alias `lecturesift-dev` is in the owner's SSH config and
uses strict host-key verification. Do not commit or copy the private key,
production environment, billing data, browser cookies or authentication cache.

Codex CLI 0.153.4 was installed from the official standalone installer in the
development account. Its command resolves in the login shell. Account login
and desktop connection activation must be completed through the supported
user authorization flow; installing the CLI does not authenticate it or move
the current task to that host.

After authentication, enable `lecturesift-dev` in desktop Settings > Connections
> SSH and save the remote checkout. A task can be handed off only after the host
is connected and the matching repository project is saved. Until that is
verified, report SSH commands as remote and this task as local; do not claim that
all browser sessions or the whole desktop have migrated.

## Access and operating boundaries

- Prefer scoped SSH keys and provider OAuth/API connections over saved passwords.
- Do not disable MFA, CAPTCHA, passkeys or account security prompts to reduce
  interruptions. Initial pairing and occasional provider reauthentication can
  require the owner.
- Never reuse a production administrator key in the development host config.
- Keep approval and sandbox controls enabled. No blanket auto-approve settings.
- Do not stop OVH PostgreSQL/Redis or its staging reverse proxy to run tests.
- Keep development jobs bounded; idle SSH access is not a background work
  schedule, production deployment or guarantee of unattended execution.

## References

- [OpenAI remote connections](https://learn.chatgpt.com/docs/remote-connections)
- [OpenAI authentication](https://learn.chatgpt.com/docs/auth)
- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [Browser smoke contract](../tests/browser/README.md)
