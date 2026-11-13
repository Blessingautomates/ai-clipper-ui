"""Local GPG integration tests; PostgreSQL executables are replaced in a temp copy.

Run: python3 tests/test_backup_gpg.py
No database connection, real dump, or existing GPG home is used.
"""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ops/toolstackai-supabase-backup.sh"
DB_PASSWORD = r"dummy:db\password"
PASSPHRASE = "dummy backup passphrase with spaces"

PG_STUB = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["TEST_AUDIT"], "a") as f:
    f.write(json.dumps({"tool": name, "args": args}) + "\n")
if args == ["--version"]:
    print(name + " (PostgreSQL) 17.11")
    sys.exit(0)
assert os.environ["PGHOST"] == "dummy.invalid"
assert os.environ["PGSSLMODE"] == "verify-full"
assert os.environ["PGOPTIONS"] == "-c default_transaction_read_only=on"
assert "PGPASSWORD" not in os.environ
pgpass = pathlib.Path(os.environ["PGPASSFILE"])
assert pgpass.stat().st_mode & 0o777 == 0o600
assert pgpass.parent.stat().st_mode & 0o777 == 0o700
assert pgpass.read_text() == "dummy.invalid:5432:postgres:dummy-user:dummy\\:db\\\\password\n"
if name == "psql":
    print("170011" if "--command=SHOW server_version_num" in args else "3\t0\t1")
elif name == "pg_dump":
    sys.stdout.buffer.write(b" TABLE public clip_jobs \n TABLE DATA public clip_jobs \n POLICY public clip_jobs \n ACL public TABLE clip_jobs \n")
    if os.environ["TEST_FAILURE"] == "producer":
        sys.exit(1)
elif name == "pg_dumpall":
    assert "--no-role-passwords" in args
    print("CREATE ROLE dummy_role;")
elif name == "pg_restore":
    data = sys.stdin.buffer.read()
    if "--list" in args:
        sys.stdout.buffer.write(data)
    else:
        assert args == ["--file=/dev/null"]
'''

GPG_WRAPPER = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
home = pathlib.Path(args[args.index("--homedir") + 1])
assert home.stat().st_mode & 0o777 == 0o700
assert "--no-autostart" in args and "--pinentry-mode" in args
assert "--no-symkey-cache" in args and "--passphrase-fd" in args
assert args[args.index("--passphrase-fd") + 1] == "3"
assert "--passphrase" not in args
assert os.fstat(3).st_mode & 0o777 == 0o600
assert "TS_BACKUP_PASSPHRASE" not in os.environ
agent = subprocess.run([os.environ["TEST_CONNECT_AGENT"], "--homedir", str(home),
                        "GETINFO pid", "/bye"], capture_output=True, text=True, check=True)
pid = int(next(line[2:] for line in agent.stdout.splitlines() if line.startswith("D ")))
with open(os.environ["TEST_AUDIT"], "a") as f:
    f.write(json.dumps({"tool": "gpg", "args": args, "home": str(home), "pid": pid}) + "\n")
failure = os.environ["TEST_FAILURE"]
out = args[args.index("--output") + 1] if "--output" in args else ""
if failure == "preflight" or (failure == "encrypt" and out.endswith("database.dump.gpg.partial")) or (failure == "roles" and out.endswith("roles.sql.gpg.partial")):
    if out:
        pathlib.Path(out).write_bytes(b"deliberately incomplete ciphertext")
    sys.exit(1)
if failure == "decrypt" and "--decrypt" in args and args[-1].endswith("database.dump.gpg"):
    sys.exit(1)
if failure == "agent":
    subprocess.run([os.environ["TEST_REAL_GPGCONF"], "--homedir", str(home), "--kill", "all"], check=True)
if failure == "wrong-passphrase" and "--decrypt" in args and args[-1].endswith("database.dump.gpg"):
    wrong = pathlib.Path(os.environ["TEST_WRONG_PASSPHRASE"])
    wrong.write_text("deliberately wrong dummy passphrase\n")
    wrong.chmod(0o600)
    fd = os.open(wrong, os.O_RDONLY)
    os.dup2(fd, 3, inheritable=True)
    if fd != 3:
        os.close(fd)
os.execv(os.environ["TEST_REAL_GPG"], [os.environ["TEST_REAL_GPG"], *args])
'''


class BackupGpgTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="toolstack-backup-integration-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir(mode=0o700)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.foreign_home = self.root / "existing-gnupg"
        self.foreign_home.mkdir(mode=0o700)
        (self.foreign_home / "sentinel").write_text("leave existing files alone")
        self.audit = self.root / "audit.jsonl"
        self.cert = self.root / "dummy-cert"
        self.cert.write_text("Dummy; read only by PostgreSQL stand-ins.")
        for tool in ("psql", "pg_dump", "pg_dumpall", "pg_restore"):
            self.write_executable(tool, PG_STUB)
        self.write_executable("gpg", GPG_WRAPPER)
        source = SCRIPT.read_text()
        pinned_path = "TS_PG_BIN=/usr/lib/postgresql/17/bin"
        self.assertEqual(source.count(pinned_path), 1)
        # Only this disposable copy can reach the fake PostgreSQL programs.
        self.script = self.root / "backup.sh"
        self.script.write_text(source.replace(pinned_path, "TS_PG_BIN=" + shlex.quote(str(self.bin))))
        self.env = dict(os.environ, HOME=str(self.home), TMPDIR=str(self.root),
                        GNUPGHOME=str(self.foreign_home), PATH=str(self.bin) + ":" + os.environ["PATH"],
                        TEST_AUDIT=str(self.audit), TEST_REAL_GPG=shutil.which("gpg"),
                        TEST_REAL_GPGCONF=shutil.which("gpgconf"),
                        TEST_WRONG_PASSPHRASE=str(self.root / "wrong-passphrase"),
                        TEST_CONNECT_AGENT=shutil.which("gpg-connect-agent"), TEST_FAILURE="")

    def write_executable(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o700)

    def run_backup(self, failure="", test_only=False):
        self.env["TEST_FAILURE"] = failure
        command = ["bash", str(self.script)]
        if test_only:
            command.append("--test-encryption")
        inputs = "\n".join(("dummy.invalid", "dummy-user", str(self.cert),
                            DB_PASSWORD, PASSPHRASE, PASSPHRASE)) + "\n"
        result = subprocess.run(command, input=inputs, env=self.env, text=True,
                                capture_output=True, timeout=30)
        events = [json.loads(line) for line in self.audit.read_text().splitlines()]
        output = result.stdout + result.stderr + self.audit.read_text()
        self.assertNotIn(DB_PASSWORD, output)
        self.assertNotIn(PASSPHRASE, output)
        for event in events:
            if event["tool"] != "gpg":
                continue
            self.assertFalse(Path(event["home"]).exists(), "GPG home was not cleaned")
            for _ in range(100):
                try:
                    # A reaped or zombie agent cannot retain live sockets/resources.
                    state = Path(f"/proc/{event['pid']}/stat").read_text().split(") ", 1)[1][0]
                except FileNotFoundError:
                    break
                if state == "Z":
                    break
                time.sleep(0.02)
            else:
                self.fail("Temporary GPG agent is still running")
        self.assertFalse(list(self.root.glob("toolstack-backup-credentials-*")))
        self.assertEqual(list(self.foreign_home.iterdir()), [self.foreign_home / "sentinel"])
        self.assertEqual((self.foreign_home / "sentinel").read_text(), "leave existing files alone")
        # Each recorded dummy test path must also disappear after every exit.
        for event in events:
            for arg in event["args"]:
                if arg.startswith("/tmp/toolstack-backup-gpg-test-"):
                    self.assertFalse(Path(arg).parent.exists())
        return result, events

    def test_dummy_round_trip_without_postgresql(self):
        result, events = self.run_backup(test_only=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("round-trip: PASS", result.stdout)
        self.assertEqual([e["tool"] for e in events], ["gpg", "gpg"])
        self.assertFalse((self.home / "toolstack-backups").exists())

    def test_preflight_failure_never_reaches_database(self):
        result, events = self.run_backup("preflight")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("database backup was not started", result.stderr)
        self.assertTrue(all(e["tool"] == "gpg" or e["args"] == ["--version"] for e in events))
        self.assertFalse((self.home / "toolstack-backups").exists())

    def test_missing_agent_never_reaches_database(self):
        result, events = self.run_backup("agent")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No agent running", result.stderr)
        self.assertIn("database backup was not started", result.stderr)
        self.assertTrue(all(e["tool"] == "gpg" or e["args"] == ["--version"] for e in events))
        self.assertFalse((self.home / "toolstack-backups").exists())

    def test_success_with_dummy_database_streams(self):
        result, _ = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        backup, = (self.home / "toolstack-backups").iterdir()
        self.assertFalse((backup / "INCOMPLETE").exists())
        self.assertTrue((backup / "SHA256SUMS").exists())
        self.assertTrue((backup / "archive_verified_utc.txt").exists())

    def test_failures_keep_incomplete_marker_and_remove_partials(self):
        for failure in ("encrypt", "roles", "decrypt", "producer", "wrong-passphrase"):
            with self.subTest(failure=failure):
                result, _ = self.run_backup(failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("backup is incomplete at:", result.stderr)
                backups = list((self.home / "toolstack-backups").iterdir())
                backup = max(backups, key=lambda p: p.stat().st_mtime_ns)
                self.assertTrue((backup / "INCOMPLETE").exists())
                self.assertFalse(list(backup.glob("*.partial")))
                self.assertFalse((backup / "archive_verified_utc.txt").exists())
                if failure in ("encrypt", "producer"):
                    self.assertFalse((backup / "database.dump.gpg").exists())
                else:
                    self.assertTrue((backup / "database.dump.gpg").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
