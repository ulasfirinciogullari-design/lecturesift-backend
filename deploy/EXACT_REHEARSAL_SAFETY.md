# Exact release staging and rehearsal gate

The exact rehearsal is a fail-closed release admission, not a general-purpose
test command. It must run only while the production API, worker, Caddy and
scheduled jobs are stopped.

## Trust boundary

The rehearsal is **not** a sandbox for hostile or unreviewed release code.
`run_exact_rehearsal.sh` and the helpers it calls execute as root and control
Docker; access to the Docker daemon is host-root-equivalent. They can also read
the root-only source/production configuration needed to create isolated inputs
and inspect live invariants. Consequently, their own evidence cannot establish
that arbitrary candidate code was safe. The admission is meaningful only after
an independently reviewed source tree is explicitly authorized.

Two fixed programs form the outer boundary. The fixed
`/usr/local/sbin/lecturesift-release-stage-controller` bounds and authenticates
the archive and bundle, imports the bundle in a private root-only directory,
compares both canonical trees, and matches the result to the private review
allowlist before executing even the candidate staging shell. The thin,
revision-pinned operator wrapper invokes only this controller; it never clones
the candidate or executes a helper from it.

The fixed
`/usr/local/sbin/lecturesift-exact-rehearsal-controller` is that outer trust
boundary. Install it from `deploy/trusted_exact_rehearsal_controller.sh` only
after review, as a root-owned non-writable file. Before it executes any candidate
helper, it requires a root-only
`/etc/lecturesift/exact-rehearsal-allowlist/REVISION.allow` record binding the
installed trusted-controller and trusted-stage-controller hashes, full canonical Git-tree hash, exact
`run_exact_rehearsal.sh` hash and the explicit
`reviewed_docker_root_equivalent=true` decision. It independently recomputes the
tree from Git objects and rejects a dirty/writable/linked worktree before the
stager may evaluate any candidate Dockerfile. In rehearsal mode it additionally
cross-checks the staged evidence, mints a private one-run nonce handoff, and
accepts completion only when that consumed handoff binds the resulting
admission. Direct execution of the candidate orchestrator has no handoff and
cannot create an admission. Do not derive or approve that allowlist solely from
candidate-produced evidence; calculate and review the values out of band.

This gate prevents accidental drift and execution of a tree that was not
allowlisted. It cannot contain a malicious tree that an operator deliberately
approved, a modified trusted controller that root approved, a compromised root
account, or a compromised Docker daemon. Exercise untrusted code only on a
disposable non-production host with synthetic data and credentials.

## Evidence chain

Before staging or rehearsal on OVH, run
`sudo bash /opt/lecturesift/deploy/check_shell_syntax.sh`. It parses every
tracked deployment shell script with `/bin/bash -n` and must emit
`SHELL_SYNTAX_OK`; Linux CI runs the same mandatory gate. Native Windows tests
skip the runtime gate only when a usable Bash is unavailable.

1. The installed trusted stage controller accepts one full 40-hex revision,
   one root-only transport archive and one root-only Git bundle. Before any
   bundle import it enforces transport size/hash and host disk/process resource
   bounds. Before any candidate shell it compares every archive path, entry
   type, executable bit and streamed file digest with the bundle revision and
   the version-2 private allowlist. Benign tracked attributes such as line-ending
   rules are allowed, but Git's effective attributes are evaluated at the exact
   revision for every tracked file and directory. Any effective `export-ignore`
   or `export-subst`, or any repository-local `info/attributes` source, is
   rejected so root-executed files cannot be hidden or transformed outside the
   reviewed digest.
2. The now-reviewed `stage_release_candidate.sh` repeats transport/tree checks.
   Before either candidate Docker build, the stager invokes the fixed controller
   in `authorize-build` mode. Missing or mismatched allowlist, source-tree,
   orchestrator, or controller hashes stop the run before Docker evaluates the
   candidate tree.
3. Only transport equality plus that independent pre-build authorization can create
   `/var/lib/lecturesift/release-candidates/REVISION.ok`. The evidence binds the
   canonical tree digest and the two staged image IDs to the revision.
4. The separately installed trusted controller accepts only the exact reviewed
   controller/tree/orchestrator hash tuple in the revision-specific root-only
   allowlist. It removes a safe stale admission for the same revision, creates a
   root-owned single-use nonce/hash handoff, and only then may execute candidate
   `run_exact_rehearsal.sh`. The candidate atomically consumes the handoff; the
   controller validates the completion/admission binding and removes any
   admission left by a failed child or failed completion check.
5. `run_exact_rehearsal.sh` validates the staged candidate before touching the
   disposable clone. It parses PostgreSQL identifiers as dotenv data; it never
   executes the master dotenv. Its final production-role login proof uses the
   official PostgreSQL container's read-only `psql`; production API/worker env
   files are never passed to the candidate application image.
6. A root-only outer `EXIT` gate runs after success, ordinary failure and
   signals handled by the shell. It removes labeled rehearsal resources and
   proves that the complete container, volume, network and TCP/UDP-listener
   inventories returned to their baselines. It also compares the main database
   manifest, the full `pg_database` inventory (owner, encoding, collation,
   locale provider/version, connection flags, comment length/digest and ACL
   digest), PostgreSQL cluster roles/password digests/settings/memberships,
   database ACL/ownership/default-grant metadata and a salted,
   value-confidential Redis key/type/value/expiry snapshot.
7. Success atomically creates
   `/var/lib/lecturesift/rehearsal-admissions/REVISION.ok`, binding the staged
   image IDs, the exact local images actually exercised, and the candidate
   evidence hash. Because separately built OCI images need not have identical
   IDs, equivalence is defined by a stronger source criterion: the candidate's
   canonical path/type/executable-bit/content tree SHA-256 must equal a fresh
   canonical inventory of `git archive REVISION`, and both image families must
   carry that exact revision in their OCI label and baked environment. The
   admission records both tree hashes and `source_tree_equivalent=true`. No
   cutover step should accept a revision without this file. Normal production
   preflight runs `validate_rehearsal_admission.py`, recomputes that Git-tree
   digest, rehashes the candidate evidence, and requires all four current image
   IDs/labels/environments to remain identical to the admission. Bootstrap and
   disaster-restore validation retain their separate explicit proof paths.

The outer gate retains the complete manifest and verifies both the pre- and
post-rehearsal main database with `verify_schema_transition.py current` and the
reviewed provider-session schema contract. `TABLE_DIFF`, `SCHEMA_COMPAT`,
`UNVALIDATED_FK`, any non-zero `ANOMALY`, a missing `SCHEMA_OBJECT`, or contract
drift rejects admission before the canonical before/after comparison.

Rehearsal containers and volumes must carry both
`lecturesift.rehearsal=true` and `lecturesift.rehearsal.run=RUN`. Any new
rehearsal code that creates a Docker resource must preserve this contract.
Under its outer lock and before taking any baseline, the gate invokes the
inner orchestrator's locked `--reconcile-only` mode. That mode never drops a
database or role: it removes only a fully validated marker older than one hour
after proving its bound database and all three derived roles (clone owner, API
and worker) are absent, fsyncs the
registry, and emits the exact marker-only success record. Recent, malformed or
unknown markers, any rehearsal database/role, or a non-empty registry after
reconciliation blocks the run and admission.

The inner stack never consumes the production API/worker dotenv files. A
host-only generator parses production and rehearsal dotenv files strictly as
data, proves all sensitive rehearsal values differ, and emits short-lived
root-owned role allowlists. Different credential strings and bucket names are
not treated as proof that storage is dedicated. Before any candidate container
starts, a separate direct-HTTPS SigV4 gate positively proves the rehearsal
identity can list its rehearsal bucket and receives `NoSuchKey` for a random,
read-only missing-object GET. The identical identity must then receive only an
explicit `AccessDenied` or `InvalidAccessKeyId` response for both operations
against the production bucket. A success, redirect, ambiguous S3 error, DNS/TLS
or transport failure blocks admission. The gate never sends a write or delete,
and its root-private, secret-free negative-capability artifact is hashed into
the exact rehearsal admission. Each clone has a timestamp-derived owner, API and
worker role; the candidate migration sees only the clone-owner URL. The API is
on a dedicated labeled internal rehearsal backend network with no host-published
ingress. It binds only to loopback inside its own container, where fixed
allowlisted health paths are probed as the unprivileged application user. It
has its own generated R2-only proxy container and alias. The worker has a different proxy container,
alias and policy: it permits R2 plus `api.openai.com` only when a dedicated
non-production rehearsal AI key exists. Separate temporary internal Docker
networks carry the two role-to-proxy links, so the API cannot use or resolve
through the worker proxy. Only the validated production PostgreSQL service is
temporarily attached to the dedicated network under the `postgres` alias used
by clone-only roles. Production Redis is never attached, and runtime probes in
both candidate roles require its service names to remain unresolvable.
The inner stack hands that temporary PostgreSQL attachment to the locked outer
orchestrator only after every stack health and isolation proof succeeds. Before
the first E2E, both candidate roles must then resolve `postgres`, connect as
their exact clone-bound role, select the exact clone database, PING the isolated
Redis service, and re-prove queue, storage and worker rollout continuity. The
outer `EXIT` cleanup retains sole ownership after handoff and disconnects
PostgreSQL only after the application, format and purge E2Es finish or fail.
The hard purge enumerates every direct `billing_users` foreign key. Its one
owner-only compatibility surface, `billing_email_verifications.user_id`, is
excluded from API-role DELETE and SELECT statements only after PostgreSQL's
catalog proves that exact foreign key is unique, single-column, immediate and
`NO ACTION`. An unexpected compatibility row therefore blocks the parent user
delete and rolls back the transaction; the owner-run final manifest also proves
the compatibility table remained unchanged. No runtime grant or owner secret is
introduced for cleanup.
The standalone R2 write/read roundtrip is attributed at creation time to the
already verified rehearsal user. This lets the same proof-bound user purge
remove its two ledger rows; provider, timestamp and null-field predicates are
never used to broaden cost cleanup.
Instagram credentials are intentionally absent; its health probe must therefore
return HTTP 503 with the exact safe `LS-IG-01` detail code rather than contact a
live provider.
A runtime gate requires the production API, worker, Caddy, and especially the
production `egress-proxy` container to remain stopped before the build, before candidate
start, and immediately before E2E probes. Production preflight also refuses any
surviving rehearsal-labeled resource or PostgreSQL rehearsal attachment after
an untrappable crash.
Arbitrary proxy and direct Internet probes must fail, while R2 health must pass.
An exact admission additionally requires a dedicated non-production OpenAI key
and successful MP3 and MP4 AI/audio/video format cases. Without that key, the
runner may produce explicit skipped-case diagnostics for document/OCR
troubleshooting, but the artifact validator rejects the result before admission
creation. Any skipped format case is non-admitting.
Durable format evidence is bound to each exact case rather than accepted as a
global lower bound. Every case reopens a PDF sample and the complete ZIP from
R2; both media cases also reopen and probe the packaged MP3, while the MP4 case
reopens a JPEG slide sample. The ZIP member set must exactly match the artifact
manifest, contain no paths, links, duplicate members or corruption, and contain
the same verified MP3 bytes. Full admission therefore requires the exact
case-bound 2 + 2 + 3 + 4 evidence matrix (11 named payload proofs); missing,
extra, duplicated or reassigned evidence fails closed.
The version-5 admission also binds `rehearsal_ai_provider=dedicated` into the
version-3 aggregate artifact digest, so older admissions and reports created
before this mandatory AI/media gate are rejected rather than reused.

## Trusted controller and operator wrapper regeneration

The ignored SHA-specific helpers are transport bootstraps only. They must not
delegate directly to candidate `run_exact_rehearsal.sh`: the rehearsal bootstrap
must invoke the fixed trusted controller with only the full revision in its
environment. After these tracked changes are committed:

1. record the new clean full commit SHA;
2. generate a new Git bundle and source archive from that same SHA;
3. calculate both SHA-256 transport digests;
4. independently review both trusted controller scripts, install them at
   `/usr/local/sbin/lecturesift-release-stage-controller` and
   `/usr/local/sbin/lecturesift-exact-rehearsal-controller`, and record both
   SHA-256 values;
5. independently review the complete candidate tree and orchestrator, then
   create the exact seven-field version-2 revision allowlist (`version`, `revision`,
   `source_tree_sha256`, `orchestrator_sha256`,
   `trusted_controller_sha256`, `trusted_stage_controller_sha256`,
   `reviewed_docker_root_equivalent`) as root mode
   `0600` under the root mode-`0700` allowlist directory;
6. generate the SHA-specific staging bootstrap with
   `generate_stage_release_wrapper.py`; it contains only pinned transport
   metadata and the fixed stage-controller path. Generate a rehearsal
   bootstrap containing only the SHA and fixed exact-controller path;
7. install uploads and bootstraps as root-owned mode `0400` or `0600` files;
8. run the staging bootstrap, endpoint verification, and trusted-controller
   rehearsal in that order.

The old `613456afa49d888d62d17dc561112e7d85acc119` bundle does not contain these
tracked guards or a version-2 review record and must not be used for cutover.

Never execute a controller copied into `/tmp` or directly execute a candidate
orchestrator. The controller must resolve to its fixed root-owned installed path;
candidate helpers must resolve inside the exact root-owned reviewed worktree,
match the allowlisted Git tree, and receive only the controller's allowlisted
environment.
