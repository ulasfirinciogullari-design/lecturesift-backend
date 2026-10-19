# R2 retention-lock proof

`r2_retention_probe.py` is a fail-closed, one-object proof for the production
backup bucket. It accepts only this repository shape:

`s3:https://<32-hex-account-id>.eu.r2.cloudflarestorage.com/lecturesift-production-backups/restic`

The tool must run as root with the host-only values from
`/etc/lecturesift/restic.env`. Those credentials must not be copied into the
API, worker, Instagram, or migration environment files.

Install `lecturesift-r2-retention-probe.service` as a root-owned systemd unit,
then invoke it explicitly. The unit creates its private
`/var/lib/lecturesift/recovery-drills` state directory before the strict
filesystem sandbox is applied:

```text
systemctl start lecturesift-r2-retention-probe.service
journalctl -u lecturesift-r2-retention-probe.service --since today
```

The script creates exactly one unique object under
`restic/data/.lecturesift-retention-probes/v1/`, reads it back and verifies its
SHA-256 and purpose metadata, attempts one DELETE, and reads it back again. It
reports success only when R2 explicitly identifies the rejected DELETE as an
object/bucket retention lock. A generic permission failure is not proof and
fails closed.

After the second readback, the tool opens the exact Restic repository and
atomically records a root-owned mode-`0600` proof at
`/var/lib/lecturesift/recovery-drills/r2-retention-lock.ok`. The proof binds
the probe to the repository ID and exact repository-target hashes; neither the
repository ID nor any credential is printed.

Once `/var/lib/lecturesift/provider-cutover/provider-cutover.ok` exists, the
tool refuses to run, before it creates any R2 object. The finalized
provider-cutover proof binds the exact hash of this record, and every
production start re-checks it, so replacing the record would stop every
release from starting. Before finalization, a fresh re-probe stays allowed
for the first-cutover seed and the finalizer.

The probe deliberately survives as immutable audit evidence until the 90-day
bucket-lock period expires. It is identified by its reserved prefix and these
metadata fields:

* `lecturesift-purpose=immutable-retention-probe`
* `lecturesift-probe-version=1`
* `lecturesift-retention-days=90`
* `lecturesift-probe-id=<uuid>`
* `lecturesift-sha256=<payload hash>`

The script never lists the bucket and will not address Restic packs,
snapshots, indexes, locks, keys, or configuration objects. Do not weaken the
repository-target or key-prefix checks to troubleshoot a failed run.
