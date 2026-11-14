# Supabase Free backup preparation before migration 002

Prepared 2026-11-08. Production has not been migrated. This guide contains
operator commands; none of its database commands have been executed. It adds
backup detail to [the deployment runbook](002_DEPLOYMENT_RUNBOOK.md) without
changing that runbook, the migration, existing documentation, or completed checks.

## Current Ubuntu client readiness

The runbook requires a completed backup at the paused maintenance boundary,
including original data/SQL NULLs and schema/policy/ACL/function definitions.
Its temporary migration snapshot and aggregate audit results are not backups.

The inspected session is Ubuntu 26.04 LTS, ARM64, glibc 2.43, inside the existing
Termux/proot environment. The initial inspection found no PostgreSQL clients.
On 2026-11-08, the operator subsequently authorized installing only
`postgresql-client` and its required client dependencies in this Ubuntu distro.
Installation succeeded and all four tools were verified locally:

| Tool | Verified version | Direct binary |
| --- | --- | --- |
| pg_dump | 18.6 (Ubuntu 18.6-0ubuntu0.26.04.1) | `/usr/lib/postgresql/18/bin/pg_dump` |
| pg_dumpall | 18.6 (Ubuntu 18.6-0ubuntu0.26.04.1) | `/usr/lib/postgresql/18/bin/pg_dumpall` |
| pg_restore | 18.6 (Ubuntu 18.6-0ubuntu0.26.04.1) | `/usr/lib/postgresql/18/bin/pg_restore` |
| psql | 18.6 (Ubuntu 18.6-0ubuntu0.26.04.1) | `/usr/lib/postgresql/18/bin/psql` |

The `/usr/bin` wrappers resolve to the same verified versions. No PostgreSQL
server, Supabase CLI, Docker, or Podman was installed. The existing PGlite test
runtime is not a hosted-project backup tool. Python 3, curl, GnuPG 2.4.8,
OpenSSL 3.5.5, gzip, and sha256sum are available. Approximately 64 GiB was free
before and after installation; this does not establish the remote database
size. `/tmp` is not durable storage.

### Authorized installation record — already completed

The existing Ubuntu package indexes were usable; no index refresh, OS upgrade,
Node/npm change, or additional repository was needed. The package simulation
and actual transaction both reported **0 upgraded, 4 newly installed, 0 removed**.
They installed only:

- `postgresql-client`: `18+290ubuntu1` (client metapackage).
- `postgresql-client-18`: `18.6-0ubuntu0.26.04.1`.
- `postgresql-client-common`: `290ubuntu1` (client wrappers).
- `libpq5`: `18.6-0ubuntu0.26.04.1` (required client library).

The download was 1616 kB; additional installed space was 6262 kB. Server and
documentation packages were suggestions, not installed. Normal man-db/libc-bin
cache triggers ran without upgrading those packages. `dpkg --audit` returned
no findings. No `postgres`, `initdb`, or `pg_ctl` server command was available
after installation.

These are the exact completed commands, run as root **inside the existing
Ubuntu distro**, not native Android Termux. Do not rerun the install now:

```bash
apt-get --simulate --no-install-recommends --no-upgrade install postgresql-client
apt-get --assume-yes --no-install-recommends --no-upgrade --no-remove install postgresql-client
```

For a future missing-client installation, inspect the simulation first and stop
if it proposes unrelated packages, existing-package upgrades, removals, or a
server. The flags supplement that plan review; dependency changes must still
be checked. Stop on installation failure rather than upgrading the system.

Supabase documents daily backups for Pro/Team/Enterprise and PITR as a paid
add-on. Free projects should maintain their own logical exports and off-site
copies. Do not assume the Free project's dashboard offers a restorable backup.
Do not pause/delete the Supabase project to try to manufacture one.

**This existing Ubuntu environment now has the tools needed to create encrypted
logical exports and read their archives. No Supabase connection, credential
request, database dump, SQL execution, or restore has occurred.** Server version,
connection/TLS readiness, paused writers, and the recovery target remain unverified.
A hand-built catalog exporter, REST/Table Editor CSV, or SQL Editor result export
cannot substitute for the PostgreSQL backup. Installing clients does not prove
that a backup exists or that it can be restored.

The installed PostgreSQL 18 manual says pg_dump supports source servers from
9.2 through 18, and refuses newer source majors. Output from an 18 client is
not guaranteed to restore into an older server, even when dumped from that
older version. The backup block retains a conservative **same-major recovery
gate** until the project's major and recovery plan are known. If the project's
major is below 18, stop before dumping and review a compatible restore path or
separately authorized matching client tools. Do not remove the gate solely to
get a dump to complete. No PostgreSQL server is authorized for this environment;
local archive readability checks do not replace the isolated restore rehearsal.

## Backup scope and exclusions

Use one unrestricted custom-format `pg_dump` archive, plus a separate
`pg_dumpall --roles-only --no-role-passwords` export. Keep default ownership and
ACL capture enabled. Do not restrict the database dump to `clip_jobs` or `public`,
use `--enable-row-security`, or omit owners, grants, policies, or managed schemas.
The database archive uses one consistent snapshot; the separate global-role
export requires role changes to stay paused too.

| Item | Coverage and recovery limit |
| --- | --- |
| Jobs and other accessible database data | Preserves values, IDs, SQL NULLs, sequences, and large objects supported by pg_dump. Includes accessible Auth data and Storage metadata. |
| Database definitions | Tables, constraints, indexes, ordinary functions/procedures, triggers, RLS enable/force state and policy expressions, table/column/function/schema ACLs, default privileges, owners, views/types, and supported publication definitions. Definitions belonging to extensions are normally represented by the extension declaration, not independent object definitions. |
| Roles | Attributes and memberships are exported separately. Role passwords/hashes are intentionally excluded. Existing project passwords are not changed. Restore credentials for custom roles must be recovered/reset through the normal private process. |
| Protected objects | Supabase's `postgres` is not an unrestricted PostgreSQL superuser. Any access-denied dump error makes this attempt incomplete; stop and arrange a supported method with sufficient access. Do not silently exclude failing schemas/tables and call it full. Platform/extension-managed objects may need a compatible Supabase environment even when the export succeeds. |
| Storage | Object bytes, vector-bucket contents/index services, and external files are not a PostgreSQL logical backup. Storage metadata alone cannot recreate files. Back up separately if used. |
| Vault / pgsodium / encrypted columns | Dumped ciphertext does not include the project's external root encryption key. Recovery into another project requires the original key through an existing secure key-management process. Do not put that key in this guide, shell history, a plaintext file, or chat. |
| Platform configuration | API/JWT/signing keys, Auth provider/SMTP/redirect configuration, Edge Function source and secrets, hosting/GitHub secrets, network controls, and other dashboard configuration are not covered. Record/recover through existing private configuration management if used. |
| Physical recovery and replication | No WAL/PITR, server filesystem/configuration, tablespaces' files, replication-slot state, or external subscriber state. A dump cannot recreate the exact hosted project automatically. |

The repository does not establish whether all excluded services are used. Do
not assume they are empty. For 002, restoration of jobs, original NULLs,
`public` definitions/policies/ACLs/functions, and dependencies such as Auth users
is essential. A whole-project disaster-recovery claim additionally requires
the separate service/key/configuration recovery arrangements above.

## Prerequisites before any database command

1. Use this existing Ubuntu session with the verified 18.6 clients, GnuPG 2.x
   with loopback pinentry support, Python 3, sha256sum, mktemp, and adequate private
   persistent storage. The scoped PATH below selects the direct Ubuntu client
   binaries and does not change system Node/npm or shell startup files. Before
   any connection, read the source PostgreSQL major in the project's dashboard
   and resolve the recovery version gate above. Do not install additional
   clients, a server, or other packages under this completed installation step.
2. In the correct existing project, open **Connect**. Prefer Direct on port
   5432 if IPv6 works; otherwise select **Session pooler**, port 5432, for IPv4.
   Copy its actual host and username separately. Session usernames are usually
   `postgres.<project-ref>`; Direct normally uses `postgres`. Do not construct
   a guessed pooler hostname or use transaction pooling on port 6543.
3. Have the current database administrator password privately available.
   It is a database password, not an anon/service-role API key. Do not reset
   it just for this procedure or paste a password-containing URI anywhere.
   Download the public root certificate through **Database → Settings → SSL**;
   use the trust certificate appropriate to the selected endpoint. This public
   certificate is not a secret. TLS/hostname validation must succeed.
4. Use an unrecorded private terminal. Disable shell tracing/history as shown;
   do not use a terminal recorder, tee/debug logging, `.pgpass`, a dotenv file,
   or `PGPASSWORD`. PostgreSQL prompts for its password on the terminal.
   GPG's separate strong backup passphrase is read without echo and passed
   through a pipe descriptor, never argv, an exported variable, or a file.
   Retain that passphrase in your existing password manager separately from
   the encrypted backup. Backup data may itself contain sensitive material;
   plaintext dump files are never created by these commands.
5. Prepare an isolated recovery target and operator with the required access.
   A fresh hosted Supabase project already contains managed objects; an
   ordinary empty PostgreSQL database lacks Supabase roles/extensions/Auth.
   Both need a target-specific restore plan. Archive readability alone is
   not a completed restore rehearsal.
6. Before the final migration-boundary backup, follow runbook steps 1–2:
   maintenance must block direct submissions, all queue writers/workers must
   be stopped, and active processing must finish. Keep schema/role changes
   paused. For whole-project point consistency also account for Auth, Storage,
   cron, and other application writers. The baseline below captures the
   runbook's SQL A totals plus SQL-NULL clips for recovery comparison; it is
   backup evidence, not a repeat of the completed audit or migration tests.

## Exact operator commands: inventory first

Client version verification below has already been completed in this Ubuntu
session. It is retained as the exact environment command, not a request to repeat
release tests or reinstall packages. If entering from native Termux after a
restart, enter the existing distro with `proot-distro login ubuntu --shared-tmp`.

```bash
(
  export PATH="/usr/lib/postgresql/18/bin:$PATH"
  for ts_tool in bash pg_dump pg_dumpall pg_restore psql gpg python3 sha256sum mktemp; do
    command -v "$ts_tool" || exit 1
  done
  pg_dump --version
  pg_dumpall --version
  pg_restore --version
  psql --version
  gpg --version
)
```

Each PostgreSQL command above reports `18.6 (Ubuntu 18.6-0ubuntu0.26.04.1)`.
If this changes after a restart, resolve that specific environment issue before
maintenance. Do not install a server or upgrade unrelated packages.

## Exact operator commands: paused-state encrypted backup

This block is for a later operator-executed backup; it has not been executed and
the client installation does not authorize a Supabase connection or dump now.
After every prerequisite is satisfied, start a private Bash shell, then run
the complete parenthesized block below. Answer its prompts; none request a URI,
API token, or key. Enter only the host, username, and certificate path at those
prompts. Database passwords go only into PostgreSQL's own password prompts.

```bash
bash --noprofile --norc
```

```bash
(
  set +x
  set +o history
  set -euo pipefail
  ulimit -c 0
  umask 077
  export PATH="/usr/lib/postgresql/18/bin:$PATH"
  for ts_tool in pg_dump pg_dumpall pg_restore psql gpg python3 sha256sum mktemp; do
    command -v "$ts_tool" >/dev/null || exit 1
  done

  unset PGPASSWORD PGSERVICE PGSERVICEFILE PGHOSTADDR PGOPTIONS
  export PGPASSFILE=/dev/null
  export PGDATABASE=postgres PGPORT=5432
  export PGSSLMODE=verify-full PGCONNECT_TIMEOUT=15
  export PGAPPNAME=toolstack-pre-002-backup
  export PGOPTIONS='-c default_transaction_read_only=on'
  read -r -p 'Direct or SESSION-pooler host from Connect: ' PGHOST
  read -r -p 'Database administrator username from Connect: ' PGUSER
  read -r -p 'Absolute path to the trusted public root certificate: ' PGSSLROOTCERT
  export PGHOST PGUSER PGSSLROOTCERT
  test -n "$PGHOST"
  test -n "$PGUSER"
  test -r "$PGSSLROOTCERT"

  unset TS_BACKUP_PASSPHRASE TS_BACKUP_CONFIRM
  read -r -s -p 'Strong backup encryption passphrase: ' TS_BACKUP_PASSPHRASE
  printf '\n'
  read -r -s -p 'Repeat backup encryption passphrase: ' TS_BACKUP_CONFIRM
  printf '\n'
  test -n "$TS_BACKUP_PASSPHRASE"
  test "$TS_BACKUP_PASSPHRASE" = "$TS_BACKUP_CONFIRM"
  unset TS_BACKUP_CONFIRM

  ts_encrypt() {
    gpg --no-options --batch --pinentry-mode loopback --no-symkey-cache \
      --passphrase-fd 3 --symmetric --cipher-algo AES256 --output "$1" \
      3< <(printf '%s\n' "$TS_BACKUP_PASSPHRASE")
  }
  ts_decrypt() {
    gpg --no-options --batch --pinentry-mode loopback --no-symkey-cache \
      --passphrase-fd 3 --decrypt "$1" \
      3< <(printf '%s\n' "$TS_BACKUP_PASSPHRASE")
  }

  ts_server_num=$(psql -X -W -At --set=ON_ERROR_STOP=1 --command='SHOW server_version_num')
  ts_client_major=$(pg_dump --version | python3 -c 'import re,sys; print(re.search(r"(\d+)\.", sys.stdin.read()).group(1))')
  if test "$((ts_server_num / 10000))" != "$ts_client_major"; then
    printf 'STOP: source/client majors differ; resolve restore compatibility before dumping.\n' >&2
    exit 1
  fi

  TS_BACKUP_BASE="$HOME/toolstack-backups"
  if test ! -d "$TS_BACKUP_BASE"; then
    mkdir -m 700 -- "$TS_BACKUP_BASE"
  fi
  test ! -L "$TS_BACKUP_BASE"
  chmod 700 -- "$TS_BACKUP_BASE"
  TS_BACKUP_DIR=$(mktemp -d "$TS_BACKUP_BASE/pre-002-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
  printf '%s\n' "$ts_server_num" > "$TS_BACKUP_DIR/server_version_num.txt"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$TS_BACKUP_DIR/started_utc.txt"

  psql -X -W -At -F $'\t' --set=ON_ERROR_STOP=1 \
    --command="SELECT count(*), count(*) FILTER (WHERE status = 'processing'), count(*) FILTER (WHERE clips IS NULL) FROM public.clip_jobs" \
    > "$TS_BACKUP_DIR/clip_jobs_baseline.tsv"
  python3 -c 'import pathlib,sys; a=pathlib.Path(sys.argv[1]).read_text().strip().split("\t"); assert len(a)==3 and all(x.isdigit() for x in a); assert int(a[1])==0, "STOP: processing jobs remain"' \
    "$TS_BACKUP_DIR/clip_jobs_baseline.tsv"

  if pg_dump -W --format=custom | ts_encrypt "$TS_BACKUP_DIR/database.dump.gpg.partial"; then
    mv -- "$TS_BACKUP_DIR/database.dump.gpg.partial" "$TS_BACKUP_DIR/database.dump.gpg"
  else
    printf 'STOP: database dump/encryption failed; this backup is incomplete.\n' >&2
    exit 1
  fi

  if pg_dumpall -W --roles-only --no-role-passwords | ts_encrypt "$TS_BACKUP_DIR/roles.sql.gpg.partial"; then
    mv -- "$TS_BACKUP_DIR/roles.sql.gpg.partial" "$TS_BACKUP_DIR/roles.sql.gpg"
  else
    printf 'STOP: role export/encryption failed; this backup is incomplete.\n' >&2
    exit 1
  fi

  ts_decrypt "$TS_BACKUP_DIR/database.dump.gpg" | pg_restore --list | \
    python3 -c 'import sys; s=sys.stdin.read(); required=(" TABLE public clip_jobs ", " TABLE DATA public clip_jobs ", " POLICY public clip_jobs ", " ACL public TABLE clip_jobs "); assert all(x in s for x in required), "STOP: required table/data/policy/ACL entries missing"; print("Required clip_jobs archive entries present")'
  ts_decrypt "$TS_BACKUP_DIR/database.dump.gpg" | pg_restore --file=/dev/null
  ts_decrypt "$TS_BACKUP_DIR/roles.sql.gpg" | \
    python3 -c 'import sys; s=sys.stdin.read(); assert "CREATE ROLE " in s, "STOP: role definitions missing"; print("Role export decrypts and contains definitions")'

  (
    cd "$TS_BACKUP_DIR"
    sha256sum database.dump.gpg roles.sql.gpg clip_jobs_baseline.tsv \
      server_version_num.txt started_utc.txt > SHA256SUMS
    sha256sum --check SHA256SUMS
  )
  date -u +%Y-%m-%dT%H:%M:%SZ > "$TS_BACKUP_DIR/archive_verified_utc.txt"
  unset TS_BACKUP_PASSPHRASE
  printf 'Encrypted archives verified at: %s\nRestore rehearsal is still required.\n' "$TS_BACKUP_DIR"
)
```

Every pipeline is checked with `pipefail`; a failed dump cannot be disguised by
successful encryption. Failed `.partial` files remain encrypted and must not be
treated as completed backups. Any error, permission denial, unexpected warning
about skipped objects, failed TLS check, missing policy/ACL entry, or remaining
processing job stops the process. Do not downgrade TLS, add exclusions, or bypass
RLS to suppress an error. Preserve the private failure evidence and resolve the
specific backup mechanism. A checksum proves file integrity, not restore success.

In this Ubuntu root session, `$HOME/toolstack-backups` is `/root/toolstack-backups`
inside the existing distro's persistent filesystem. The block creates a private
directory there only when the operator later executes the backup. No backup
directory or artifact was created during client installation. Keep an independent
encrypted copy outside the device/distro; another directory on the same phone
does not protect against device loss.

`pg_restore --list` checks archive structure; the separate `--file=/dev/null`
pass decompresses/renders all archive payloads without exposing SQL/data and
without connecting to any database. The aggregate baseline and timestamps contain
no connection credentials. The passphrase exists only in the subshell's memory
and pipe descriptors; core dumps are disabled. Normal local terminal privacy and
trusted-host access controls remain prerequisites.

## Restore verification and the recovery gate

Before calling the backup restorable, the recovery operator must:

1. Copy the encrypted backup directory to independent private storage, retaining
   the passphrase separately. Run the independent-copy verification block below
   on that copy; do not depend on a cached key or the original device alone.
2. Reconcile the encrypted role export against an isolated recovery target.
   `postgres` and Supabase-managed roles may already exist; blindly executing
   `roles.sql` usually conflicts and must not change production role attributes.
   Recreate needed custom roles/memberships only in that target. Provide matching
   extensions and external root keys where needed. Resolve managed-schema
   collisions through the target-specific plan, retaining all application
   definitions/data/ACLs rather than ignoring restore errors.
3. Rehearse the restore into that prepared target, retaining private results.
   A full archive can be restored with the command below only if the target
   has been prepared to accept all its objects. It deliberately has no `--clean`,
   `--no-owner`, or `--no-acl`. Do not point it at the existing production project
   or assume an initialized hosted Supabase project can accept it unchanged.

Verify the independent copy with this complete block. It connects to no database
and displays no plaintext dump content:

```bash
(
  set +x
  set +o history
  set -euo pipefail
  ulimit -c 0
  export PATH="/usr/lib/postgresql/18/bin:$PATH"
  read -r -p 'Absolute path to the INDEPENDENT backup directory: ' TS_VERIFY_DIR
  unset TS_VERIFY_PASSPHRASE
  read -r -s -p 'Backup encryption passphrase from your password manager: ' TS_VERIFY_PASSPHRASE
  printf '\n'
  ts_verify_decrypt() {
    gpg --no-options --batch --pinentry-mode loopback --no-symkey-cache \
      --passphrase-fd 3 --decrypt "$1" \
      3< <(printf '%s\n' "$TS_VERIFY_PASSPHRASE")
  }
  cd "$TS_VERIFY_DIR"
  sha256sum --check SHA256SUMS
  ts_verify_decrypt database.dump.gpg | pg_restore --list >/dev/null
  ts_verify_decrypt database.dump.gpg | pg_restore --file=/dev/null
  ts_verify_decrypt roles.sql.gpg >/dev/null
  printf 'Independent copy decrypts and all database archive payloads are readable.\n'
)
```

After the recovery operator has prepared the isolated target, the exact restore
command is below. Every connection prompt is for **that disposable target**;
the block deliberately replaces inherited connection settings:

```bash
(
  set +x
  set +o history
  set -euo pipefail
  ulimit -c 0
  export PATH="/usr/lib/postgresql/18/bin:$PATH"
  unset PGPASSWORD PGOPTIONS PGSERVICE PGSERVICEFILE PGHOSTADDR
  export PGPASSFILE=/dev/null
  export PGSSLMODE=verify-full PGCONNECT_TIMEOUT=15
  export PGAPPNAME=toolstack-isolated-backup-restore
  read -r -p 'DISPOSABLE recovery target host (never production): ' PGHOST
  read -r -p 'DISPOSABLE recovery target port: ' PGPORT
  read -r -p 'DISPOSABLE recovery target administrator username: ' PGUSER
  read -r -p 'DISPOSABLE recovery target database name: ' PGDATABASE
  read -r -p 'Absolute path to the recovery target public root certificate: ' PGSSLROOTCERT
  test -n "$PGHOST"
  test -n "$PGPORT"
  test -n "$PGUSER"
  test -n "$PGDATABASE"
  test -r "$PGSSLROOTCERT"
  export PGHOST PGPORT PGUSER PGDATABASE PGSSLROOTCERT
  read -r -p 'Absolute path to the encrypted database archive: ' TS_RESTORE_ARCHIVE
  unset TS_RESTORE_PASSPHRASE
  read -r -s -p 'Backup encryption passphrase: ' TS_RESTORE_PASSPHRASE
  printf '\n'
  gpg --no-options --batch --pinentry-mode loopback --no-symkey-cache \
    --passphrase-fd 3 --decrypt "$TS_RESTORE_ARCHIVE" \
    3< <(printf '%s\n' "$TS_RESTORE_PASSPHRASE") | \
    pg_restore -W --exit-on-error --single-transaction --dbname="$PGDATABASE"
)
```

This is SQL execution **in the isolated recovery target**, not a command for
production and not something the agent has run. If any target prerequisite is
unknown, stop before it. PostgreSQL errors roll back this single transaction;
there must be no ignored errors or skipped required objects. `pg_restore` can
execute function definitions from the archive, so the target must be isolated
from production services/secrets and external integrations.

After successful recovery, use the target's private SQL interface to run only
the backup comparison, not migration 002 or its post-migration verification:

```sql
BEGIN READ ONLY;
SELECT count(*) AS total_jobs,
       count(*) FILTER (WHERE status = 'processing') AS processing_jobs,
       count(*) FILTER (WHERE clips IS NULL) AS sql_null_clips
FROM public.clip_jobs;
COMMIT;
```

Require all three values to match `clip_jobs_baseline.tsv`. Compare recovered
pre-002 definitions with the encrypted source schema: table/column defaults and
nullability, FK dependencies, original policy expressions and RLS state,
table/column/function ACLs and default grants, trigger/function bodies, owners,
and any other application schemas/objects. These are backup recovery checks on
the isolated target, not a repeat of the completed production audit. An empty
jobs table does not waive metadata verification. Record the tested target,
tool/server versions, completion time, exact target-specific procedure, and
remaining service/key exclusions privately.

Do not claim this is a verified whole-project recovery until its full recovery
path and exclusions are accounted for. If only a scoped 002 reversal is needed
later, extract the relevant backed-up definitions/data for a reviewed correction;
do not restore broad original access into production or overwrite newer writes.
See the existing runbook's rollback rules. This guide authorizes no production
restore, migration, deployment, commit, or push.

## Sources checked

- [Supabase database backups](https://supabase.com/docs/guides/platform/backups):
  Free-plan exports, paid backups/PITR, role password and Storage limits.
- [Supabase connection modes and SSL](https://supabase.com/docs/guides/database/connecting-to-postgres):
  Direct/session endpoints, actual pooler host, public root certificate and TLS.
- [Supabase logical backup/restore](https://supabase.com/docs/guides/platform/migrating-within-supabase/backup-restore):
  CLI/Docker prerequisites, managed schema/configuration limits, external
  Vault/pgsodium root key and restore target preparation. Its sample credential
  handling is intentionally not reproduced here.
- Installed PostgreSQL 18 pg_dump manual:
  `/usr/share/postgresql/18/man/man1/pg_dump.1.gz`; source and restore version
  compatibility was read locally after client installation, without a connection.

## Exact next action

In the existing production project's **Supabase Dashboard → Project Settings →
Infrastructure**, read its **Postgres version** and report only the version number.
This dashboard inspection supplies the missing source-version information without
using the installed clients or exposing credentials. Do not copy a connection
URI, password, or API key. The next preparation step after version compatibility
is resolved is the public SSL certificate and Direct/session connection selection;
do not activate maintenance, run the dump block, or execute migration 002 yet.
