#!/usr/bin/env bash

(
  set +x
  set +o history
  set -euo pipefail
  ulimit -c 0
  umask 077
  TS_TEST_ENCRYPTION=0
  if test "$#" != 0; then
    if test "$#" = 1 && test "$1" = --test-encryption; then
      TS_TEST_ENCRYPTION=1
    else
      printf 'Usage: bash %s [--test-encryption]\n' "$0" >&2
      exit 1
    fi
  fi
  TS_PG_BIN=/usr/lib/postgresql/17/bin
  if test "$TS_TEST_ENCRYPTION" != 1; then
    for ts_tool in pg_dump pg_dumpall pg_restore psql; do
      if test ! -x "$TS_PG_BIN/$ts_tool"; then
        printf 'STOP: required PostgreSQL 17.11 tool missing: %s\n' "$TS_PG_BIN/$ts_tool" >&2
        exit 1
      fi
      ts_tool_version=$("$TS_PG_BIN/$ts_tool" --version)
      case "$ts_tool_version" in
        "$ts_tool (PostgreSQL) 17.11" | "$ts_tool (PostgreSQL) 17.11 "*) ;;
        *)
          printf 'STOP: %s must be PostgreSQL 17.11.\n' "$TS_PG_BIN/$ts_tool" >&2
          exit 1
          ;;
      esac
    done
  fi
  for ts_tool in gpg gpgconf cmp python3 sha256sum mktemp chmod rm rmdir; do
    command -v "$ts_tool" >/dev/null || exit 1
  done

  TS_CREDENTIAL_DIR=
  TS_GPG_HOME=
  TS_LOCAL_TEST_DIR=
  TS_BACKUP_DIR=
  TS_BACKUP_COMPLETE=0
  ts_cleanup() {
    local ts_exit_status=$?
    trap - EXIT HUP INT TERM
    set +e
    unset TS_DB_PASSWORD TS_BACKUP_PASSPHRASE TS_BACKUP_CONFIRM PGPASSFILE
    if test -n "$TS_GPG_HOME"; then
      local ts_socket_dir
      if ! ts_socket_dir=$(gpgconf --homedir "$TS_GPG_HOME" --list-dirs socketdir); then
        printf 'STOP: could not inspect temporary GPG socket directory.\n' >&2
        ts_exit_status=1
      fi
      if gpgconf --homedir "$TS_GPG_HOME" --kill all; then
        # /run/user socket directories, when available, are outside the home.
        if test -n "$ts_socket_dir" && test "$ts_socket_dir" != "$TS_GPG_HOME"; then
          if ! gpgconf --homedir "$TS_GPG_HOME" --remove-socketdir; then
            printf 'STOP: could not remove temporary GPG socket directory: %s\n' "$ts_socket_dir" >&2
            ts_exit_status=1
          fi
        fi
        if ! rm -rf -- "$TS_GPG_HOME"; then
          printf 'STOP: could not remove temporary GPG home: %s\n' "$TS_GPG_HOME" >&2
          ts_exit_status=1
        fi
      else
        printf 'STOP: could not stop temporary GPG resources at: %s\n' "$TS_GPG_HOME" >&2
        ts_exit_status=1
      fi
    fi
    if test -n "$TS_LOCAL_TEST_DIR"; then
      if ! rm -rf -- "$TS_LOCAL_TEST_DIR"; then
        printf 'STOP: could not remove local GPG test files: %s\n' "$TS_LOCAL_TEST_DIR" >&2
        ts_exit_status=1
      fi
    fi
    if test -n "$TS_CREDENTIAL_DIR"; then
      if ! rm -f -- "$TS_CREDENTIAL_DIR/pgpass" "$TS_CREDENTIAL_DIR/backup-passphrase" ||
         ! rmdir -- "$TS_CREDENTIAL_DIR"; then
        printf 'STOP: could not remove temporary credential files at: %s\n' "$TS_CREDENTIAL_DIR" >&2
        ts_exit_status=1
      fi
    fi
    if test -n "$TS_BACKUP_DIR" && test "$TS_BACKUP_COMPLETE" != 1; then
      rm -f -- "$TS_BACKUP_DIR/database.dump.gpg.partial" "$TS_BACKUP_DIR/roles.sql.gpg.partial"
      printf 'STOP: backup is incomplete at: %s\n' "$TS_BACKUP_DIR" >&2
    fi
    exit "$ts_exit_status"
  }
  trap ts_cleanup EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM

  ts_init_gpg() {
    # A short, private path avoids missing $HOME/.gnupg and long proot sockets.
    TS_GPG_HOME=$(mktemp -d /tmp/toolstack-backup-gpg-XXXXXXXX) || return 1
    chmod 700 -- "$TS_GPG_HOME" || return 1
    gpgconf --homedir "$TS_GPG_HOME" --launch gpg-agent
  }
  ts_encrypt() {
    gpg --no-options --homedir "$TS_GPG_HOME" --no-autostart \
      --batch --pinentry-mode loopback --no-symkey-cache \
      --passphrase-fd 3 --symmetric --cipher-algo AES256 --output "$1" \
      3< "${2:-$TS_CREDENTIAL_DIR/backup-passphrase}"
  }
  ts_decrypt() {
    gpg --no-options --homedir "$TS_GPG_HOME" --no-autostart \
      --batch --pinentry-mode loopback --no-symkey-cache \
      --passphrase-fd 3 --decrypt "$1" \
      3< "${2:-$TS_CREDENTIAL_DIR/backup-passphrase}"
  }
  ts_local_encryption_test() {
    TS_LOCAL_TEST_DIR=$(mktemp -d /tmp/toolstack-backup-gpg-test-XXXXXXXX) || return 1
    printf 'Local backup encryption test\nBinary data: \000\001\377\n' > "$TS_LOCAL_TEST_DIR/original" || return 1
    printf '%s\n' 'dummy local test passphrase' > "$TS_LOCAL_TEST_DIR/passphrase" || return 1
    ts_encrypt "$TS_LOCAL_TEST_DIR/encrypted.gpg" "$TS_LOCAL_TEST_DIR/passphrase" \
      < "$TS_LOCAL_TEST_DIR/original" || return 1
    ts_decrypt "$TS_LOCAL_TEST_DIR/encrypted.gpg" "$TS_LOCAL_TEST_DIR/passphrase" \
      > "$TS_LOCAL_TEST_DIR/decrypted" || return 1
    cmp -- "$TS_LOCAL_TEST_DIR/original" "$TS_LOCAL_TEST_DIR/decrypted" || return 1
    rm -rf -- "$TS_LOCAL_TEST_DIR" || return 1
    TS_LOCAL_TEST_DIR=
    printf 'Local GPG encryption/decryption round-trip: PASS\n'
  }

  if test "$TS_TEST_ENCRYPTION" = 1; then
    if ! ts_init_gpg || ! ts_local_encryption_test; then
      printf 'STOP: local GPG encryption test failed.\n' >&2
      exit 1
    fi
    exit 0
  fi

  unset PGPASSWORD PGSERVICE PGSERVICEFILE PGHOSTADDR PGOPTIONS
  export PGPASSFILE=/dev/null
  export PGDATABASE=postgres PGPORT=5432
  export PGSSLMODE=verify-full PGCONNECT_TIMEOUT=15
  export PGAPPNAME=toolstack-pre-002-backup
  export PGOPTIONS='-c default_transaction_read_only=on'
  IFS= read -r -p 'Direct or SESSION-pooler host from Connect: ' PGHOST
  IFS= read -r -p 'Database administrator username from Connect: ' PGUSER
  IFS= read -r -p 'Absolute path to the trusted public root certificate: ' PGSSLROOTCERT
  export PGHOST PGUSER PGSSLROOTCERT
  test -n "$PGHOST"
  test -n "$PGUSER"
  test -r "$PGSSLROOTCERT"

  TS_CREDENTIAL_DIR=$(mktemp -d "${TMPDIR:-/tmp}/toolstack-backup-credentials-XXXXXXXX")
  chmod 700 -- "$TS_CREDENTIAL_DIR"
  unset TS_DB_PASSWORD TS_BACKUP_PASSPHRASE TS_BACKUP_CONFIRM
  IFS= read -r -s -p 'Database password (used for this backup run only): ' TS_DB_PASSWORD
  printf '\n'
  test -n "$TS_DB_PASSWORD"
  ts_pgpass_escape() {
    local ts_value=$1
    ts_value=${ts_value//\\/\\\\}
    ts_value=${ts_value//:/\\:}
    printf '%s' "$ts_value"
  }
  PGPASSFILE="$TS_CREDENTIAL_DIR/pgpass"
  printf '%s:%s:%s:%s:%s\n' \
    "$(ts_pgpass_escape "$PGHOST")" "$PGPORT" "$PGDATABASE" \
    "$(ts_pgpass_escape "$PGUSER")" "$(ts_pgpass_escape "$TS_DB_PASSWORD")" > "$PGPASSFILE"
  chmod 600 -- "$PGPASSFILE"
  export PGPASSFILE
  unset TS_DB_PASSWORD

  IFS= read -r -s -p 'Strong backup encryption passphrase: ' TS_BACKUP_PASSPHRASE
  printf '\n'
  IFS= read -r -s -p 'Repeat backup encryption passphrase: ' TS_BACKUP_CONFIRM
  printf '\n'
  test -n "$TS_BACKUP_PASSPHRASE"
  test "$TS_BACKUP_PASSPHRASE" = "$TS_BACKUP_CONFIRM"
  printf '%s\n' "$TS_BACKUP_PASSPHRASE" > "$TS_CREDENTIAL_DIR/backup-passphrase"
  chmod 600 -- "$TS_CREDENTIAL_DIR/backup-passphrase"
  unset TS_BACKUP_PASSPHRASE TS_BACKUP_CONFIRM

  if ! ts_init_gpg || ! ts_local_encryption_test; then
    printf 'STOP: local GPG encryption test failed; database backup was not started.\n' >&2
    exit 1
  fi

  ts_server_num=$("$TS_PG_BIN/psql" -X -w -At --set=ON_ERROR_STOP=1 --command='SHOW server_version_num')
  ts_client_major=$("$TS_PG_BIN/pg_dump" --version | python3 -c 'import re,sys; print(re.search(r"(\d+)\.", sys.stdin.read()).group(1))')
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
  printf 'This backup is incomplete until archive verification succeeds.\n' > "$TS_BACKUP_DIR/INCOMPLETE"
  printf '%s\n' "$ts_server_num" > "$TS_BACKUP_DIR/server_version_num.txt"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$TS_BACKUP_DIR/started_utc.txt"

  "$TS_PG_BIN/psql" -X -w -At -F $'\t' --set=ON_ERROR_STOP=1 \
    --command="SELECT count(*), count(*) FILTER (WHERE status = 'processing'), count(*) FILTER (WHERE clips IS NULL) FROM public.clip_jobs" \
    > "$TS_BACKUP_DIR/clip_jobs_baseline.tsv"
  python3 -c 'import pathlib,sys; a=pathlib.Path(sys.argv[1]).read_text().strip().split("\t"); assert len(a)==3 and all(x.isdigit() for x in a); assert int(a[1])==0, "STOP: processing jobs remain"' \
    "$TS_BACKUP_DIR/clip_jobs_baseline.tsv"

  if "$TS_PG_BIN/pg_dump" -w --format=custom | ts_encrypt "$TS_BACKUP_DIR/database.dump.gpg.partial"; then
    mv -- "$TS_BACKUP_DIR/database.dump.gpg.partial" "$TS_BACKUP_DIR/database.dump.gpg"
  else
    printf 'STOP: database dump/encryption failed; this backup is incomplete.\n' >&2
    exit 1
  fi

  if "$TS_PG_BIN/pg_dumpall" -w --roles-only --no-role-passwords | ts_encrypt "$TS_BACKUP_DIR/roles.sql.gpg.partial"; then
    mv -- "$TS_BACKUP_DIR/roles.sql.gpg.partial" "$TS_BACKUP_DIR/roles.sql.gpg"
  else
    printf 'STOP: role export/encryption failed; this backup is incomplete.\n' >&2
    exit 1
  fi

  ts_decrypt "$TS_BACKUP_DIR/database.dump.gpg" | "$TS_PG_BIN/pg_restore" --list | \
    python3 -c 'import sys; s=sys.stdin.read(); required=(" TABLE public clip_jobs ", " TABLE DATA public clip_jobs ", " POLICY public clip_jobs ", " ACL public TABLE clip_jobs "); assert all(x in s for x in required), "STOP: required table/data/policy/ACL entries missing"; print("Required clip_jobs archive entries present")'
  ts_decrypt "$TS_BACKUP_DIR/database.dump.gpg" | "$TS_PG_BIN/pg_restore" --file=/dev/null
  ts_decrypt "$TS_BACKUP_DIR/roles.sql.gpg" | \
    python3 -c 'import sys; s=sys.stdin.read(); assert "CREATE ROLE " in s, "STOP: role definitions missing"; print("Role export decrypts and contains definitions")'

  (
    cd "$TS_BACKUP_DIR"
    sha256sum database.dump.gpg roles.sql.gpg clip_jobs_baseline.tsv \
      server_version_num.txt started_utc.txt > SHA256SUMS
    sha256sum --check SHA256SUMS
  )
  date -u +%Y-%m-%dT%H:%M:%SZ > "$TS_BACKUP_DIR/archive_verified_utc.txt"
  rm -- "$TS_BACKUP_DIR/INCOMPLETE"
  TS_BACKUP_COMPLETE=1
  printf 'Encrypted archives verified at: %s\nRestore rehearsal is still required.\n' "$TS_BACKUP_DIR"
)
