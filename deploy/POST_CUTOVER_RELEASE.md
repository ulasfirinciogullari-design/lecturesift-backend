# Post-cutover release procedure

Use this procedure for every OVH release after the provider cutover. It
applies only once the cutover is settled:

- the final proof `provider-cutover.ok` is valid against its own release
  revision (the cutover revision);
- the one-time provider first start is consumed and valid;
- the durable ingress state is `activated`;
- the payment-provider baseline validates against the cutover revision.

A newer release revision passes the read-only production gates
(`validate-final`, `first-start-status`, `ensure-ingress-freeze`,
`ingress-status` and the baseline `--check`) only in that settled state. Each
gate still validates the evidence against the cutover revision, so nothing is
rebound to the new release. The new release's own identity is proved by its
exact rehearsal admission and by the health revision checks, which use the new
revision. Every mutating cutover step stays pinned to the cutover revision:
ingress handoff and transitions, arming a required first start, first-start
recovery, finalization, rollback to Render, migrations and seeding. None of
them can run for a later release.

On every start, reload and reboot, `verify_provider_first_start.sh` first
checks whether the first start is already consumed. When it is, `arm` and
`complete` succeed without the Instagram publisher gate, so a reboot with
Instagram automation enabled no longer stops the core. A required or armed
first start, an unprovable status and every `recover` still require all
Instagram publishers to be stopped.

The commands below are the existing tools. Run them on the OVH host in
this order. Read [EXACT_REHEARSAL_SAFETY.md](EXACT_REHEARSAL_SAFETY.md) for
the trust boundary of staging and rehearsal.

## Until the first release with these fixes is live

The cutover release `0739d39` that production runs today still has the old
verifier. Until a release containing these fixes has been promoted, every
restart, `systemctl reload lecturesift`, Docker restart or host reboot runs
the Instagram publisher gate before the first-start check, and fails while
Instagram automation is on. Before any of those on `0739d39`, turn all
Instagram automation off:

- set `INSTAGRAM_DAILY_AUTOMATION_ENABLED=false` in
  `/etc/lecturesift/runtime.env` (preflight regenerates `instagram.env` from
  it on start);
- disable and stop `lecturesift-instagram.timer` and make sure
  `lecturesift-instagram.service` is inactive.

The same applies when step 6 restarts `0739d39` after an abandoned release.

Never re-run `lecturesift-r2-retention-probe.service` after the cutover. The
finalized provider-cutover proof binds the exact hash of
`/var/lib/lecturesift/recovery-drills/r2-retention-lock.ok`, and every start
re-checks it. A new probe record would stop every release from starting.
Releases with these fixes refuse to re-probe once the cutover is final. The
probe in `0739d39` does not refuse, so never start it by hand.

## 0. Before you start

- The release revision is reviewed, merged and green in CI.
- The transports, the version-2 allowlist record
  `/etc/lecturesift/exact-rehearsal-allowlist/$REVISION.allow` and the
  installed trusted controllers follow steps 1 to 7 of "Trusted controller
  and operator wrapper regeneration" in
  [EXACT_REHEARSAL_SAFETY.md](EXACT_REHEARSAL_SAFETY.md). Calculate and
  review the allowlist values independently; never derive them from
  candidate-produced evidence.
- `/etc/lecturesift/rehearsal.env` holds the dedicated non-production R2
  and OpenAI rehearsal credentials described in `VPS_DEPLOYMENT.md`.
- The rehearsal still clones the frozen Render database named by
  `/root/.lecturesift-render-source.env`. Keep that file and the frozen
  Render database.

Record the identities and the current unit enablement, and prove that the
cutover is settled on the running release:

```sh
REVISION=<exact-40-hex-reviewed-commit>
CURRENT="$(sudo sed -n 's/^LECTURESIFT_EXPECTED_BUILD_REVISION=//p' /run/lecturesift/release.env)"
systemctl is-enabled lecturesift.service lecturesift-ingress-selector.service \
  lecturesift-backup.timer lecturesift-instagram.timer
sudo bash /opt/lecturesift/deploy/verify_production_ingress.sh activated
```

`CURRENT` is the release you return to if this release is abandoned or rolled
back. Keep the `is-enabled` output so you restore exactly what was enabled.

## 1. Pause scheduled jobs

Instagram job containers and the backup's worker restart would change the
container state that staging compares before and after its builds. The unit
installer that promotion runs also requires both timers to be disabled.

```sh
sudo systemctl disable --now lecturesift-instagram.timer
systemctl is-active lecturesift-instagram.service
sudo systemctl stop lecturesift-instagram.service
sudo systemctl disable --now lecturesift-backup.timer
systemctl is-active lecturesift-backup.service
```

If `lecturesift-instagram.service` is still running a publication, wait until
`is-active` prints `inactive` or `failed` before you stop it. Never stop a
running backup. Wait until `lecturesift-backup.service` is `inactive` or
`failed`.

## 2. Stage the candidate (production keeps serving)

Install the reviewed archive and bundle as root-owned mode `0400` or `0600`
files at `/var/tmp/lecturesift-$REVISION.tar` and
`/var/tmp/lecturesift-$REVISION.bundle`. On the reviewing workstation,
generate the revision-pinned stage wrapper from the reviewed checkout:

```sh
python3 deploy/generate_stage_release_wrapper.py --revision "$REVISION" \
  --archive-sha256 <archive-sha256> --bundle-sha256 <bundle-sha256> \
  --output "stage_exact_release_$REVISION.sh"
```

Install the wrapper on the host as a root-owned file, then run it as root.
It takes no arguments and invokes only the fixed
`/usr/local/sbin/lecturesift-release-stage-controller`:

```sh
sudo bash -p <path-to>/stage_exact_release_$REVISION.sh
```

Staging creates `/var/lib/lecturesift/release-candidates/$REVISION.ok` and the
`lecturesift-backend:staged-$REVISION` and
`lecturesift-egress-proxy:staged-$REVISION` images. It does not touch the
running production containers.

Staging leaves its build cache behind. The core start in step 6 refuses to
run unless the Docker filesystem keeps the 10 GiB host reserve, one
maximum-size job (8 GiB) and one maximum upload (1 GiB with the default video
limit) free: `20401094656` bytes, about 20.4 GB. Check it now, while
production still serves:

```sh
df -B1 --output=avail "$(sudo docker info --format '{{.DockerRootDir}}')"
```

If it prints less than `20401094656`, remove the unused build cache:

```sh
sudo docker builder prune -f
```

This removes build cache only, never images, so the current release stays
restorable. If space is still short, remove the images of revisions older
than `CURRENT` as described in "Prune old images" before you go on. They
belong to neither of the two releases you keep.

## 3. Stop production

Public traffic stops here. Stop the selector first so that it cannot restart
ingress, then stop ingress and the private core. The unit installer requires
the core to be disabled. The selector stays enabled; it is the only boot owner
and must only be inactive.

```sh
sudo systemctl stop lecturesift-ingress-selector.service
sudo systemctl stop lecturesift-ingress.service lecturesift.service
sudo systemctl disable lecturesift.service
```

`lecturesift.service` stops PostgreSQL and Redis too. You do not start them by
hand: the rehearsal does it in step 4.

If the host reboots inside this window, the enabled selector pulls the core
back in. Before step 4 that restarts the current release. After step 4 the
current release refuses to start, because the rehearsal retags its images. In
that case either finish the promotion or follow "Abandon before promotion"
below.

## 4. Rehearse

```sh
sudo bash /opt/lecturesift/deploy/check_shell_syntax.sh
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
  LECTURESIFT_EXPECTED_REHEARSAL_REVISION="$REVISION" \
  /usr/local/sbin/lecturesift-exact-rehearsal-controller
```

The syntax gate must print `SHELL_SYNTAX_OK`. The rehearsal first proves that
every production unit is inactive and every production API, worker, proxy and
Caddy container is stopped. Under its lock it then starts exactly the
production `postgres` and `redis` services, only from
`/opt/lecturesift/compose.yaml` and only if they are not already running and
healthy. It then proves again that no other production container started.
If they cannot be started and proved healthy, it fails with
`EXACT_REHEARSAL_FAILED|production-datastores-unavailable` before any
baseline is taken.

Success prints `EXACT_REHEARSAL_ADMITTED|revision=$REVISION` and writes
`/var/lib/lecturesift/rehearsal-admissions/$REVISION.ok`. The rehearsal also
retags `lecturesift-backend:local` and `lecturesift-egress-proxy:local` to the
images of `$REVISION`.

On every exit, the rehearsal stops the PostgreSQL and Redis containers that it
started. It does this after its after-state check and before it writes the
admission, so a failed stop never leaves an admission behind. This matters
because promotion moves `/opt/lecturesift` aside. A running container would
keep the old tree's bind mounts, such as `deploy/redis.conf`, and a later
`compose up` would keep that container. Stopping them means the step 6 start
mounts the files of the promoted release.

Before step 5, confirm that both are stopped. This command must print `false`
twice:

```sh
sudo docker container inspect --format '{{.State.Running}}' \
  lecturesift-postgres-1 lecturesift-redis-1
```

If either is still running, for example because it was already running before
the rehearsal, stop both before you promote:

```sh
sudo docker compose --project-directory /opt/lecturesift \
  --file /opt/lecturesift/compose.yaml stop --timeout 600 redis postgres
```

## 5. Promote

Promotion after the first one never uses the one-time legacy-root
confirmation:

```sh
sudo env LECTURESIFT_PROMOTE_RELEASE_CONFIRM=PROMOTE-EXACT-REHEARSED-RELEASE \
  bash "/srv/lecturesift/worktrees/$REVISION/deploy/promote_rehearsed_release.sh" \
  "$REVISION"
```

Continue only after
`REHEARSED_RELEASE_PROMOTED|revision=$REVISION|previous=...|services_started=false`.
The previous tree is kept under
`/opt/.lecturesift-previous/lecturesift-<UTC-stamp>-$CURRENT`. If
`/var/lib/lecturesift/release-promotion/release-promotion.in-progress`
remains, the rollback is unproven. Keep every service stopped and
investigate. Do not delete the marker.

## 6. Start

```sh
sudo systemctl enable lecturesift.service
sudo systemctl start lecturesift.service
sudo systemctl start lecturesift-ingress-selector.service
```

Preflight proves the settled cutover for the cutover revision and the exact
rehearsal admission of `$REVISION`. The first-start verifier then reports
`Provider first-start gate was already consumed.`. The selector reconciles the
activated state and starts `lecturesift-ingress.service`.

## 7. Verify and resume scheduled jobs

```sh
sudo bash /opt/lecturesift/deploy/verify_production_ingress.sh activated
```

It must print
`PRODUCTION_INGRESS_GATE_OK|mode=activated|revision=$REVISION`. The selector
reconciles every 30 seconds, so rerun the gate once if ingress was still
starting. This gate checks identity, revision and readiness only. It is not
proof that payments, email or every media format work. Run the release's own
acceptance checks for the areas it changed.

Then re-enable only the timers that step 0 recorded as enabled:

```sh
sudo systemctl enable --now lecturesift-backup.timer
sudo systemctl enable --now lecturesift-instagram.timer
```

## Rollback

There are two cases. In both, a rollback target must be either the cutover
revision itself or a release that contains this procedure's gate changes.
A post-cutover release without them, such as `e0d6491`, pins every gate to its
own revision and can never start.

The rollback target must also still pass its rehearsal admission. That needs:

- its worktree under `/srv/lecturesift/worktrees/`;
- its candidate evidence and admission;
- its `staged-<revision>` images;
- its local images;
- the installed trusted controllers named in its allowlist.

The prune rule below keeps all of these for the previous release. A code
rollback does not undo database migrations that the newer release applied at
start. Roll back only to a release whose schema contract accepts the current
database.

### Restore the local image tags

`validate_rehearsal_admission.py` binds `lecturesift-backend:local` and
`lecturesift-egress-proxy:local` to the exact image IDs that the target's
admission recorded. Every rehearsal retags them. Before you start or promote a
different release, restore them from that release's admission. Set `TARGET`
to `$CURRENT` (abandon) or to the previous admitted revision (rollback):

```sh
TARGET=<exact-40-hex-admitted-revision>
ADMISSION="/var/lib/lecturesift/rehearsal-admissions/$TARGET.ok"
APP_ID="$(sudo sed -n 's/^rehearsed_local_app_image_id=//p' "$ADMISSION")"
PROXY_ID="$(sudo sed -n 's/^rehearsed_local_proxy_image_id=//p' "$ADMISSION")"
sudo docker image tag "$APP_ID" lecturesift-backend:local
sudo docker image tag "$PROXY_ID" lecturesift-egress-proxy:local
```

### Abandon before promotion

If staging, rehearsal or promotion failed and promotion did not report
`REHEARSED_RELEASE_PROMOTED`, `/opt/lecturesift` is still `$CURRENT`:

1. Restore the local image tags with `TARGET=$CURRENT`. If the rehearsal
   never retagged them, this changes nothing.
2. If `$CURRENT` is the cutover revision `0739d39`, turn all Instagram
   automation off first, as described under "Until the first release with
   these fixes is live".
3. Run steps 6 and 7. For `0739d39`, do not re-enable the Instagram timer.

### Roll back after promotion

1. Run step 1 if the timers were re-enabled, then step 3.
2. If the target is the cutover revision, turn all Instagram automation off
   first (see below).
3. Restore the local image tags with `TARGET=<previous-admitted-revision>`.
4. Promote it with the step 5 command, using `TARGET` in place of
   `$REVISION`.
5. Run steps 6 and 7 with `TARGET` as the expected revision.

The cutover revision predates the consumed-first-start fix. Its verifier runs
the Instagram publisher gate on every start, reload and reboot, so it starts
only while all Instagram automation is off:

- `INSTAGRAM_DAILY_AUTOMATION_ENABLED=false` in `/etc/lecturesift/runtime.env`.
  Preflight regenerates `instagram.env` from it on start.
- `lecturesift-instagram.timer` stays disabled for as long as it runs. Do not
  re-enable it in step 7.

## Prune old images

Prune only after the new release has passed step 7. Keep two releases: the
current release and the previous one. For each, keep:

- the `staged-<revision>` tags;
- the local image IDs from its admission;
- its admission and candidate evidence;
- its worktree.

After the next rehearsal, the previous release's local images are untagged
and are identified only by the IDs in its admission. Never run
`docker image prune` or `docker system prune`. Either one deletes those
untagged images and with them the rollback.

Remove the images of each revision `OLD` that is older than the previous
release:

```sh
OLD=<exact-40-hex-revision-older-than-the-previous-release>
OLD_ADMISSION="/var/lib/lecturesift/rehearsal-admissions/$OLD.ok"
OLD_APP_ID="$(sudo sed -n 's/^rehearsed_local_app_image_id=//p' "$OLD_ADMISSION")"
OLD_PROXY_ID="$(sudo sed -n 's/^rehearsed_local_proxy_image_id=//p' "$OLD_ADMISSION")"
sudo docker image rm "lecturesift-backend:staged-$OLD" "lecturesift-egress-proxy:staged-$OLD"
sudo docker image rm "$OLD_APP_ID" "$OLD_PROXY_ID"
```

`docker image rm` without `--force` refuses an image that a container still
uses. Never add `--force`, and never remove `lecturesift-backend:local`,
`lecturesift-egress-proxy:local` or the previous release's images.

## Failure alerts

From the first release that contains `lecturesift-failure-alert@.service`,
systemd emails the operators when one of these units fails:

- `lecturesift.service`, the private core, when a start, restart or stop
  fails, including a start at boot or after a Docker restart. A failed
  `systemctl reload lecturesift` leaves the unit active, so each reload step
  runs through `deploy/core_reload_step.sh`, which queues the alert itself
  and keeps the step's exit status;
- `lecturesift-instagram.service`, the 15-minute Instagram job;
- `lecturesift-ingress-selector.service`. Its loop retries a failed
  reconciliation instead of exiting, so it queues the alert itself after each
  failed attempt. `OnFailure=` also covers systemd giving up on the unit.

The scheduled backup keeps its own `lecturesift-backup-alert@.service` email.

The email goes to `LECTURESIFT_OPS_ALERT_EMAIL` and to every address in
`LECTURESIFT_ADMIN_EMAILS`, through the Resend or SMTP account configured in
`/etc/lecturesift/runtime.env`. It is short. It says which unit failed on
which host and when, in UTC and in Turkey time. It gives the unit's state,
result and main-process exit status as `systemctl show` reports them, and the
command that shows the log on the server
(`journalctl -u <unit> -n 100`). It carries no log lines, because a log
excerpt can hold credentials or customer data; read the journal on the
server instead. When the core or the selector is still active, the email
says that a reload or one reconciliation attempt failed.

More than ten recipients get the alert in several messages of at most ten
each. Only addresses the provider accepted count as reached. A Resend answer
without a message id is not a delivery, and the alert unit's journal counts
the addresses an SMTP server refused. Sending stops after 60 seconds, well inside the alert unit's 90-second
limit, and the journal of the alert unit says how many recipients were
reached. Each unit gets at most one alert per hour, however many messages it
took. The alert counts as soon as its first message is delivered. The time of
the last one is in `/var/lib/lecturesift/failure-alerts/<unit>.last-sent`. A
delivery failure is only logged in the alert unit's journal and never affects
the failed unit.

Stopping `lecturesift-instagram.service` while it still runs can end it as
failed and send an alert. Step 1 already waits for it to finish. Promotion
refuses to run while an alert is still being sent, which takes at most 90
seconds.

Test the alert once after the first release that contains it. This sends one
real email and touches no other unit:

```sh
sudo systemctl start lecturesift-failure-alert@lecturesift-instagram.service.service
journalctl -u lecturesift-failure-alert@lecturesift-instagram.service.service -n 20
```

The journal must show
`Failure alert for lecturesift-instagram.service sent to N recipient(s).`, and
the email must arrive. Resend accepting the request is not proof of delivery.
The test email reports the job's real current state, so a healthy job shows
as `inactive (dead)` with result `success`. A second start within the hour
logs `suppressed` instead. To repeat the test
sooner, remove the record first:

```sh
sudo rm -f /var/lib/lecturesift/failure-alerts/lecturesift-instagram.service.last-sent
```
