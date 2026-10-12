import concurrent.futures
import json
import contextlib
import getpass
import io
import os
import shlex
import subprocess
import sys
import socket
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ingestion.repo_manager import RepoManager, RepositoryOperationError, _run_git
from src.ingestion.ssh_support import (
    GitContext, Identity, SSHError, _effective, inspect_private_key,
    launcher_main, materialize_configuration, run_checked,
)
from src.service import MultiRepoRAGService
from src.cli.main import cmd_sync
from src.mcp.server import MCPServer
from src.server.api import make_request_handler
from http_fixture import attach_http_context
from test_ssh_keys import generate_key


class TestGitSSH(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.service = MultiRepoRAGService(str(self.root / "data"), embedding_provider="subword")
        self.addCleanup(self.service.shutdown)
        self.manager = self.service.repo_manager
        self.keys = [self.manager.register_ssh_key(name, str(generate_key(self.root / name)))
                     for name in ("A", "B")]
        self.repo = self.manager.register_repo(
            "git", "Git", "git", "git@fixture.invalid:repo",
            assignment={"ssh_key_id": self.keys[0]["key_id"]},
        )
        self.checkout = self.manager.get_repo_root_path(self.repo)
        (self.checkout / ".git").mkdir(parents=True)
        (self.checkout / "app.py").write_text("def fixture(): return 1\n")
        self.environment = patch.dict(os.environ, {"GIT_SSH": "", "GIT_SSH_COMMAND": "", "GIT_SSH_VARIANT": "ssh"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_effective_target_identity_and_routing_with_quoted_paths_and_proxy_hops(self):
        for proxy in ("ProxyJump hop", 'ProxyCommand sh -c "exec nc %h %p"'):
            with self.subTest(proxy=proxy), tempfile.TemporaryDirectory(dir=self.root) as directory:
                config = self.root / "source-config"
                known = self.root / "known hosts"
                config.write_text(
                    'Host alias\n HostName target.fixture.invalid\n User configured\n Port 2222\n'
                    f' IdentityFile "{self.keys[1]["private_key_path"]}"\n IdentityFile /another\n'
                    ' CertificateFile /unrelated-cert\n ForwardAgent yes\n'
                    ' IdentityAgent /unrelated-agent\n NoHostAuthenticationForLocalhost yes\n'
                    f' UserKnownHostsFile="{known}"\n {proxy}\n'
                    f'Host hop\n HostName hop.fixture.invalid\n User jump\n IdentityFile "{self.keys[1]["private_key_path"]}"\n'
                )
                identity = Identity(self.keys[0]["private_key_path"], self.keys[0]["fingerprint"])
                generated = materialize_configuration(["-F", str(config), "-o", "SendEnv=GIT_PROTOCOL", "-p", "3333", "explicit@alias"], identity, Path(directory))
                effective, _ = _effective(["-F", str(generated), "-o", "SendEnv=GIT_PROTOCOL", "-p", "3333", "explicit@alias"])
                self.assertEqual(effective["identityfile"], [str(Path(directory) / "identity")])
                self.assertEqual((Path(directory) / "identity").resolve(), Path(identity.private_key_path))
                self.assertEqual(effective["certificatefile"], ["none"])
                self.assertEqual(effective["hostname"], ["target.fixture.invalid"])
                self.assertEqual(effective["user"], ["explicit"])
                self.assertEqual(effective["port"], ["3333"])
                self.assertEqual(effective["userknownhostsfile"], [str(known)])
                for key, value in (("batchmode", "yes"), ("identitiesonly", "yes"),
                                   ("forwardagent", "no"), ("preferredauthentications", "publickey"),
                                   ("identityagent", os.environ.get("SSH_AUTH_SOCK") or "none"),
                                   ("nohostauthenticationforlocalhost", "no"),
                                   ("passwordauthentication", "no"), ("kbdinteractiveauthentication", "no")):
                    self.assertEqual(effective[key], [value])
                self.assertEqual(generated.stat().st_mode & 0o777, 0o600)
                hop, _ = _effective(["-F", str(generated), "hop"])
                self.assertEqual(hop["identityfile"], [self.keys[1]["private_key_path"]])
                self.assertEqual(hop["user"], ["jump"])
                self.assertEqual(config.read_text().count("IdentityFile"), 3)

    def test_ambiguous_tokens_unsupported_settings_and_missing_tools_fail_before_network(self):
        config = self.root / "ambiguous"
        config.write_text('Host alias\n UserKnownHostsFile "/tmp/known hosts"\nHost other\n UserKnownHostsFile /tmp/known hosts\n')
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            with self.assertRaises(SSHError) as caught:
                materialize_configuration(["-F", str(config), "alias"],
                                          Identity(self.keys[0]["private_key_path"], self.keys[0]["fingerprint"]), Path(directory))
            self.assertEqual(caught.exception.code, "ssh_config_unsupported")
        with patch("src.ingestion.ssh_support.run_checked", side_effect=FileNotFoundError()):
            with self.assertRaises(SSHError) as caught:
                _effective(["fixture.invalid"])
            self.assertEqual(caught.exception.code, "ssh_tool_unavailable")

    def test_saved_assignment_first_clone_shared_snapshot_and_local_only_missing_key(self):
        observed = []
        changed = False

        def git(arguments, **options):
            nonlocal changed
            if arguments[1:3] == ["config", "--get"]:
                return subprocess.CompletedProcess(arguments, 1, "", "")
            if arguments[1] == "clone":
                root = Path(arguments[-1])
                (root / ".git").mkdir(parents=True)
                (root / "app.py").write_text("def first_fixture(): pass\n")
            command = shlex.split(options["env"].get("GIT_SSH_COMMAND", ""))
            if command and command[-2].endswith("ssh_support.py"):
                directory = Path(command[-1])
                metadata = json.loads((directory / "profile.json").read_text())
                observed.append((arguments[1], metadata["fingerprint"], directory))
                self.assertEqual(options["env"]["GIT_ALLOW_PROTOCOL"], "ssh")
                if arguments[1] == "fetch" and not changed:
                    changed = True
                    # A concurrent operation has a separate ContextVar scope.
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                        executor.submit(self.manager.save_configuration, self.manager.get_repo("git"),
                                        {"ssh_key_id": self.keys[1]["key_id"]}).result()
            return subprocess.CompletedProcess(arguments, 0, "a" * 40, "")

        with patch("src.ingestion.repo_manager.run_checked", side_effect=git):
            self.service.update_repository("git", branch="feature", assignment={"ssh_key_id": self.keys[0]["key_id"]})
        self.assertEqual({fingerprint for _, fingerprint, _ in observed}, {self.keys[0]["fingerprint"]})
        self.assertEqual(self.manager.get_repo("git").ssh_key_id, self.keys[1]["key_id"])
        self.assertTrue(all(not directory.exists() for _, _, directory in observed))
        observed.clear()
        with patch("src.ingestion.repo_manager.run_checked", side_effect=git):
            self.service.add_repository("first", "First", "git", "git@fixture.invalid:first",
                                        assignment={"ssh_key_id": self.keys[0]["key_id"]})
        self.assertEqual(observed[0][:2], ("clone", self.keys[0]["fingerprint"]))
        Path(self.keys[1]["private_key_path"]).unlink()
        with patch("src.ingestion.repo_manager.run_checked", return_value=subprocess.CompletedProcess([], 0, "b" * 40, "")) as run:
            self.assertEqual(self.service.sync_repository("git", local_only=True)["status"], "indexed_local")
            self.assertEqual([c.args[0][1] for c in run.call_args_list], ["rev-parse"])
            self.assertEqual(run.call_args.kwargs["env"]["GIT_ALLOW_PROTOCOL"], "")
            self.assertEqual(run.call_args.kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")
        before = self.manager.get_repo("git").last_synced_at
        with self.assertRaises(RepositoryOperationError) as caught:
            self.service.sync_repository("git")
        self.assertEqual(caught.exception.code, "ssh_key_unavailable")
        self.assertIsNone(caught.exception.config_saved)
        self.assertEqual(self.manager.get_repo("git").last_synced_at, before)
        self.assertTrue(self.service.search("fixture", repo_ids=["git"]))

    def test_no_sync_still_authenticates_and_changed_file_stops_later_phase(self):
        def git(arguments, **options):
            if arguments[1] == "config":
                return subprocess.CompletedProcess(arguments, 1, "", "")
            if arguments[1] == "fetch":
                Path(self.keys[0]["private_key_path"]).write_bytes(Path(self.keys[1]["private_key_path"]).read_bytes())
            return subprocess.CompletedProcess(arguments, 0, "", "")
        with patch("src.ingestion.repo_manager.run_checked", side_effect=git) as run:
            with self.assertRaises(RepositoryOperationError) as caught:
                self.service.update_repository("git", branch="feature", auto_sync=False)
            self.assertEqual(caught.exception.code, "ssh_key_changed")
            self.assertTrue(caught.exception.config_saved)
            self.assertEqual([c.args[0][1] for c in run.call_args_list if c.args[0][1] != "config"], ["fetch"])

    def test_custom_launchers_conflict_only_for_managed_mode_and_system_environment_survives(self):
        context = GitContext("git", Identity(self.keys[0]["private_key_path"], self.keys[0]["fingerprint"]))
        for override in ({"GIT_SSH": "/custom"}, {"GIT_SSH_COMMAND": "custom"}, {"GIT_SSH_VARIANT": "plink"}):
            with patch.dict(os.environ, {**override, "SSH_AUTH_SOCK": "/fixture-agent"}):
                before = dict(os.environ)
                with self.assertRaises(RepositoryOperationError) as caught:
                    _run_git(["fetch", "origin"], "fetch", context=context)
                self.assertEqual(caught.exception.code, "ssh_override_conflict")
                with patch("src.ingestion.repo_manager.run_checked", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
                    _run_git(["fetch", "origin"], "fetch")
                    self.assertEqual(run.call_args.kwargs["env"]["SSH_AUTH_SOCK"], "/fixture-agent")
                    for name, value in override.items():
                        self.assertEqual(run.call_args.kwargs["env"][name], value)
                self.assertEqual(dict(os.environ), before)
        with patch("src.ingestion.repo_manager.run_checked", return_value=subprocess.CompletedProcess([], 0, "custom", "")):
            with self.assertRaises(RepositoryOperationError) as caught:
                _run_git(["fetch", "origin"], "fetch", context=context)
            self.assertEqual(caught.exception.code, "ssh_override_conflict")

    def test_system_ssh_preserves_native_core_command_precedence(self):
        marker = self.root / "core-command-used"
        script = self.root / "custom-core-ssh.py"
        script.write_text(
            "import sys\nfrom pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('called')\n"
            "sys.stderr.write('Permission denied (publickey).\\n')\nsys.exit(1)\n"
        )
        config = self.root / "core-config"
        subprocess.run(["git", "config", "--file", str(config), "core.sshCommand",
                        shlex.join([sys.executable, str(script)])],
                       check=True, capture_output=True, timeout=15)
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"}):
            before = dict(os.environ)
            with self.assertRaises(RepositoryOperationError) as caught:
                _run_git(["ls-remote", "git@fixture.invalid:repo"], "fetch")
            self.assertEqual(caught.exception.code, "git_authentication_failed")
            self.assertTrue(marker.exists())
            marker.unlink()
            context = GitContext("git", Identity(self.keys[0]["private_key_path"], self.keys[0]["fingerprint"]))
            with self.assertRaises(RepositoryOperationError) as caught:
                _run_git(["ls-remote", "git@fixture.invalid:repo"], "fetch", context=context)
            self.assertEqual(caught.exception.code, "ssh_override_conflict")
            self.assertFalse(marker.exists())
            self.assertEqual(dict(os.environ), before)

    def test_real_timeout_reaps_only_operation_descendants(self):
        marker = self.root / "child.pid"
        program = (
            "import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); "
            f"open({str(marker)!r},'w').write(str(p.pid)); time.sleep(30)"
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            run_checked([sys.executable, "-c", program], timeout=0.3)
        child = int(marker.read_text())
        for _ in range(40):
            result = subprocess.run(["ps", "-o", "stat=", "-p", str(child)], capture_output=True, text=True)
            if not result.stdout.strip() or result.stdout.strip().startswith("Z"):
                break
            time.sleep(0.05)
        self.assertTrue(not result.stdout.strip() or result.stdout.strip().startswith("Z"))

    def test_common_runner_denies_managed_credentials_before_inspection(self):
        context = GitContext("git", Identity(self.keys[0]["private_key_path"],
                                            self.keys[0]["fingerprint"]), ssh_allowed=False)
        with patch("src.ingestion.repo_manager.run_checked") as run:
            with self.assertRaises(RepositoryOperationError) as caught:
                _run_git(["fetch", "origin"], "fetch", context=context)
        self.assertEqual((caught.exception.code, caught.exception.phase),
                         ("ssh_http_forbidden", "http_policy"))
        run.assert_not_called()

    def test_managed_git_rewrite_cannot_switch_to_https(self):
        config = self.root / "rewrite-config"
        config.write_text('[url "https://fixture.invalid/"]\n insteadOf = git@fixture.invalid:\n')
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"}):
            with self.assertRaises(RepositoryOperationError) as caught:
                self.service.add_repository(
                    "rewritten", "Rewritten", "git", "git@fixture.invalid:repo",
                    assignment={"ssh_key_id": self.keys[0]["key_id"]},
                )
        self.assertEqual((caught.exception.code, caught.exception.phase),
                         ("ssh_config_unsupported", "ssh_configuration"))
        self.assertTrue(caught.exception.config_saved)
        self.assertEqual(self.manager.get_repo("rewritten").ssh_key_id, self.keys[0]["key_id"])

    def test_launcher_network_tool_failure_has_safe_code_and_removes_target_configuration(self):
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            directory = Path(temporary)
            (directory / "profile.json").write_text(json.dumps({
                "private_key_path": self.keys[0]["private_key_path"],
                "fingerprint": self.keys[0]["fingerprint"],
            }))
            with patch.object(sys, "argv", ["ssh_support.py", temporary, "fixture.invalid", "git-upload-pack repo"]):
                with patch("src.ingestion.ssh_support.materialize_configuration", return_value=directory / "config"):
                    with patch("src.ingestion.ssh_support.subprocess.run", side_effect=FileNotFoundError("SENTINEL_SECRET")):
                        self.assertEqual(launcher_main(), 80)
            self.assertEqual((directory / "error").read_text(), "ssh_tool_unavailable")
            self.assertEqual({path.name for path in directory.iterdir()}, {"profile.json", "error"})

    def test_concurrent_repository_profiles_use_separate_snapshots_and_directories(self):
        other = self.manager.register_repo(
            "other", "Other", "git", "git@fixture.invalid:other",
            assignment={"ssh_key_id": self.keys[1]["key_id"]},
        )
        (self.manager.get_repo_root_path(other) / ".git").mkdir(parents=True)
        barrier = threading.Barrier(2)
        observations = []
        before = dict(os.environ)

        def git(arguments, **options):
            if arguments[1] == "config":
                return subprocess.CompletedProcess(arguments, 1, "", "")
            directory = Path(shlex.split(options["env"]["GIT_SSH_COMMAND"])[-1])
            profile = json.loads((directory / "profile.json").read_text())
            observations.append((Path(options["cwd"]).name, profile["fingerprint"], directory))
            if arguments[1] == "fetch":
                barrier.wait(timeout=5)
            return subprocess.CompletedProcess(arguments, 0, "", "")

        with patch("src.ingestion.repo_manager.run_checked", side_effect=git):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(self.manager.clone_or_pull_git_repo, [self.repo, other]))
        self.assertEqual(results, [(True, None), (True, None)])
        for rid, fingerprint, directory in observations:
            self.assertEqual(fingerprint, self.keys[0 if rid == "git" else 1]["fingerprint"])
            self.assertFalse(directory.exists())
        self.assertEqual(len({directory for _, _, directory in observations}), len(observations))
        self.assertEqual(dict(os.environ), before)

    def test_managed_timeout_and_cancellation_remove_launcher_children_and_configuration(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        source_config = self.root / "fixture-config"
        source_config.write_text("")
        marker = self.root / "authentication-processes.json"
        git = bin_dir / "git"
        git.write_text(
            f"#!{sys.executable}\n"
            "import os,shlex,subprocess,sys\n"
            "if sys.argv[1] == 'config': sys.exit(1)\n"
            "sys.exit(subprocess.call(shlex.split(os.environ['GIT_SSH_COMMAND']) + ['fixture.invalid']))\n"
        )
        ssh = bin_dir / "ssh"
        ssh.write_text(
            f"#!{sys.executable}\n"
            "import json,os,subprocess,sys,time\n"
            "if '-G' in sys.argv:\n"
            f" os.execv('/usr/bin/ssh',['ssh','-F',{str(source_config)!r},*sys.argv[1:]])\n"
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
            f"with open({str(marker)!r},'w') as stream: json.dump([os.getpid(),child.pid],stream)\n"
            "time.sleep(30)\n"
        )
        for executable in (git, ssh):
            executable.chmod(0o700)
        agent_socket = self.root / "agent.sock"
        agent = subprocess.Popen(["ssh-agent", "-D", "-a", str(agent_socket)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if agent_socket.exists():
                    break
                time.sleep(0.01)
            self.assertTrue(agent_socket.exists())
            env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                   "SSH_AUTH_SOCK": str(agent_socket)}
            subprocess.run(["ssh-add", self.keys[1]["private_key_path"]], env=env,
                           check=True, capture_output=True, timeout=15)
            identities = subprocess.run(["ssh-add", "-l"], env=env, check=True,
                                        capture_output=True, timeout=15).stdout
            context = GitContext("git", Identity(self.keys[0]["private_key_path"], self.keys[0]["fingerprint"]))
            private_bytes = Path(self.keys[0]["private_key_path"]).read_bytes()
            communicate = subprocess.Popen.communicate
            for cancellation in (False, True):
                with self.subTest(cancellation=cancellation):
                    if marker.exists():
                        marker.unlink()
                    directories = []
                    interrupted = False

                    def checked(arguments, **options):
                        if arguments[1] == "fetch":
                            directories.append(Path(shlex.split(options["env"]["GIT_SSH_COMMAND"])[-1]))
                            if not cancellation:
                                options["timeout"] = 1.5
                        return run_checked(arguments, **options)

                    def interrupt(process, *args, **kwargs):
                        nonlocal interrupted
                        if cancellation and process.args[:2] == ["git", "fetch"] and not interrupted:
                            interrupted = True
                            for _ in range(500):
                                if marker.exists():
                                    break
                                time.sleep(0.01)
                            if not marker.exists():
                                raise AssertionError("Authentication child did not become ready")
                            raise KeyboardInterrupt()
                        return communicate(process, *args, **kwargs)

                    with patch.dict(os.environ, env), patch("src.ingestion.repo_manager.run_checked", side_effect=checked):
                        before = dict(os.environ)
                        with patch.object(subprocess.Popen, "communicate", interrupt):
                            with self.assertRaises(KeyboardInterrupt if cancellation else RepositoryOperationError) as caught:
                                _run_git(["fetch", "origin"], "fetch", context=context)
                        if not cancellation:
                            self.assertEqual(caught.exception.code, "git_timeout")
                        self.assertEqual(dict(os.environ), before)
                    self.assertTrue(marker.exists())
                    for pid in json.loads(marker.read_text()):
                        for _ in range(100):
                            status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                                    capture_output=True, text=True).stdout.strip()
                            if not status or status.startswith("Z"):
                                break
                            time.sleep(0.01)
                        self.assertTrue(not status or status.startswith("Z"))
                    self.assertTrue(directories)
                    self.assertTrue(all(not path.exists() for path in directories))
                    self.assertIsNone(agent.poll())
                    self.assertEqual(subprocess.run(["ssh-add", "-l"], env=env, check=True,
                                                    capture_output=True, timeout=15).stdout, identities)
                    self.assertEqual(Path(self.keys[0]["private_key_path"]).read_bytes(), private_bytes)
                    self.assertEqual(source_config.read_text(), "")
        finally:
            agent.terminate()
            agent.wait(timeout=5)

    def test_real_selected_system_locked_and_unlocked_identities_across_surfaces(self):
        daemon = shutil.which("sshd") or "/usr/sbin/sshd"
        if not Path(daemon).is_file():
            self.skipTest("Optional loopback SSH daemon fixture is unavailable")
        host_key = generate_key(self.root / "host")
        encrypted = generate_key(self.root / "encrypted", "synthetic-unlock-fixture")
        encrypted_profile = self.manager.register_ssh_key("Encrypted", str(encrypted))
        authorized = self.root / "authorized"
        authorized.write_text(Path(self.keys[1]["private_key_path"] + ".pub").read_text())
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server_config = self.root / "daemon-config"
        server_config.write_text(
            f"ListenAddress 127.0.0.1\nPort {port}\nHostKey {host_key}\n"
            f"PidFile {self.root}/daemon.pid\nAuthorizedKeysFile {authorized}\n"
            "StrictModes no\nUsePAM no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n"
            f"AllowUsers {getpass.getuser()}\nLogLevel VERBOSE\n"
        )
        defaults = subprocess.run([daemon, "-T", "-f", str(server_config)], check=True,
                                  capture_output=True, text=True, timeout=15)
        if "persourcepenalties " in defaults.stdout:
            with server_config.open("a") as stream:
                stream.write("PerSourcePenalties no\n")
        known = self.root / "known hosts"
        known.write_text(f"[127.0.0.1]:{port} " + Path(str(host_key) + ".pub").read_text())
        client_config = self.root / "client-config"
        client_config.write_text(
            f'Host *\n UserKnownHostsFile "{known}"\n IdentityFile "{self.keys[1]["private_key_path"]}"\n'
            " BatchMode yes\n StrictHostKeyChecking yes\n"
        )
        binary = self.root / "bin"
        binary.mkdir()
        launcher = binary / "ssh"
        launcher.write_text("#!/bin/sh\nexec /usr/bin/ssh -F " + shlex.quote(str(client_config)) + ' "$@"\n')
        launcher.chmod(0o700)
        askpass = self.root / "fixture-askpass"
        askpass.write_text("#!/bin/sh\nprintf '%s\\n' 'synthetic-unlock-fixture'\n")
        askpass.chmod(0o700)
        agent_socket = self.root / "agent.sock"
        daemon_log = (self.root / "daemon.log").open("w")
        server = subprocess.Popen([daemon, "-D", "-e", "-f", str(server_config)],
                                  stdout=subprocess.DEVNULL, stderr=daemon_log)
        agent = subprocess.Popen(["ssh-agent", "-D", "-a", str(agent_socket)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if server.poll() is not None or agent.poll() is not None:
                    self.fail("Synthetic OpenSSH fixture could not start")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        if agent_socket.exists():
                            break
                except OSError:
                    pass
                time.sleep(0.03)
            environment = {
                "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                "SSH_AUTH_SOCK": str(agent_socket), "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
            }
            with patch.dict(os.environ, environment):
                subprocess.run(["ssh-add", self.keys[1]["private_key_path"]], check=True,
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
                upstream, remote = self.root / "upstream", self.root / "remote.git"
                subprocess.run(["git", "init", "--initial-branch=main", str(upstream)],
                               check=True, capture_output=True, timeout=15)
                (upstream / "app.py").write_text("def controlled_fixture(): return 1\n")
                subprocess.run(["git", "add", "app.py"], cwd=upstream, check=True, capture_output=True, timeout=15)
                subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                "-c", "commit.gpgsign=false", "commit", "-m", "fixture"],
                               cwd=upstream, check=True, capture_output=True, timeout=15)
                subprocess.run(["git", "clone", "--bare", str(upstream), str(remote)],
                               check=True, capture_output=True, timeout=15)
                url = f"ssh://{getpass.getuser()}@127.0.0.1:{port}{remote}"
                with self.assertRaises(RepositoryOperationError) as caught:
                    self.service.add_repository("controlled", "Controlled", "git", url,
                                                assignment={"ssh_key_id": self.keys[0]["key_id"]})
                self.assertEqual(caught.exception.code, "git_authentication_failed")
                self.assertTrue(caught.exception.config_saved)
                system = self.service.update_repository("controlled", assignment={"ssh_key_id": None})
                self.assertEqual(system["sync_result"]["status"], "synced")
                authorized.write_text(Path(self.keys[0]["private_key_path"] + ".pub").read_text()
                                      + Path(self.keys[1]["private_key_path"] + ".pub").read_text()
                                      + Path(str(encrypted) + ".pub").read_text())
                selected = self.service.update_repository("controlled", assignment={"ssh_key_id": self.keys[0]["key_id"]})
                self.assertEqual(selected["sync_result"]["status"], "synced")
                before = self.manager.get_repo("controlled")
                self.service.update_repository("controlled", auto_sync=False,
                                               assignment={"ssh_key_id": encrypted_profile["key_id"]})
                handler_cls = make_request_handler(self.service, self.root / "web")
                handler = handler_cls.__new__(handler_cls)
                handler.path = "/api/v1/repos/controlled/sync"
                handler.headers = {"Content-Length": "2"}
                attach_http_context(handler)
                handler.rfile, handler.wfile = io.BytesIO(b"{}"), io.BytesIO()
                handler.send_response = lambda code: setattr(handler, "code", code)
                handler.send_header = lambda *args: None
                handler.end_headers = lambda: None
                handler.do_POST()
                self.assertEqual(handler.code, 500)
                failure = json.loads(handler.wfile.getvalue())
                self.assertEqual(failure["code"], "git_authentication_failed")
                self.assertNotIn("config_saved", failure)
                args = type("Args", (), dict(repo_id="controlled", all=False, interactive=False, force=False, local_only=False))()
                output = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    self.assertEqual(cmd_sync(self.service, args), 1)
                message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "sync_repository", "arguments": {"repo_id": "controlled"}}}
                response = MCPServer(service=self.service).handle_request(message)
                self.assertTrue(response["result"]["isError"])
                self.assertNotIn("synthetic-unlock-fixture", output.getvalue() + repr(failure) + repr(response))
                current = self.manager.get_repo("controlled")
                self.assertEqual((current.commit_hash, current.last_synced_at, current.total_chunks),
                                 (before.commit_hash, before.last_synced_at, before.total_chunks))
                self.assertTrue(self.service.search("controlled_fixture", repo_ids=["controlled"]))
                self.assertEqual(self.service.sync_repository("controlled", local_only=True)["status"], "indexed_local")
                unlocked_environment = {**os.environ, "SSH_ASKPASS": str(askpass),
                                        "SSH_ASKPASS_REQUIRE": "force", "DISPLAY": "fixture"}
                subprocess.run(["ssh-add", str(encrypted)], env=unlocked_environment,
                               check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
                self.assertEqual(self.service.sync_repository("controlled")["status"], "synced")
                listed = subprocess.run(["ssh-add", "-l", "-E", "sha256"], check=True,
                                        capture_output=True, text=True, timeout=15).stdout
                self.assertIn(self.keys[1]["fingerprint"], listed)
                self.assertIn(encrypted_profile["fingerprint"], listed)
                self.assertEqual(client_config.read_text().count("IdentityFile"), 1)
                self.assertEqual(self.keys[0]["fingerprint"], inspect_private_key(self.keys[0]["private_key_path"])[1])
        finally:
            for process in (server, agent):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
            daemon_log.close()
