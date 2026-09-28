
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading

# Suppress console windows on Windows for all subprocess calls
_HIDE_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}

from PyQt6.QtCore import QObject, pyqtSignal


class UpdateWorker(QObject):
    """Runs git fetch / pull on daemon threads and signals results.

    Signals:
        check_done(int):         commits behind origin/main.
                                 -1 = network/git error
                                 -2 = dev build (dirty working tree or local commits)
        pull_done(bool, bool):   (success, deps_changed)
        conda_update_done(bool, str):  (success, error_message)
    """

    check_done = pyqtSignal(int)
    pull_done  = pyqtSignal(bool, bool)
    conda_update_done = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        self._root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def start_check(self):
        threading.Thread(target=self._do_check, daemon=True).start()

    def start_pull(self):
        threading.Thread(target=self._do_pull, daemon=True).start()

    def start_conda_update(self):
        self._conda_proc = None  # Initialize before thread starts
        threading.Thread(target=self._do_conda_update, daemon=True).start()

    def cancel_conda_update(self):
        """Kill a running conda update subprocess, if any."""
        proc = getattr(self, "_conda_proc", None)
        if proc and proc.poll() is None:
            proc.kill()


    def _do_check(self):
        git_dir = os.path.join(self._root, ".git")
        if not os.path.isdir(git_dir):
            self.check_done.emit(-3)   # not a git install — zip download
            return
        try:
            subprocess.run(
                ["git", "fetch", "--quiet"],
                cwd=self._root, timeout=10,
                capture_output=True, check=True, **_HIDE_WINDOW,
            )
            dirty = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self._root, timeout=5,
                capture_output=True, text=True, check=True, **_HIDE_WINDOW,
            ).stdout.strip()
            ahead = subprocess.run(
                ["git", "rev-list", "origin/main..HEAD", "--count"],
                cwd=self._root, timeout=5,
                capture_output=True, text=True, check=True, **_HIDE_WINDOW,
            ).stdout.strip()
            if dirty or int(ahead) > 0:
                self.check_done.emit(-2)   # dev build — don't offer update
                return
            r = subprocess.run(
                ["git", "rev-list", "HEAD..origin/main", "--count"],
                cwd=self._root, timeout=5,
                capture_output=True, text=True, check=True, **_HIDE_WINDOW,
            )
            self.check_done.emit(int(r.stdout.strip()))
        except Exception:
            self.check_done.emit(-1)

    def _env_hash(self) -> str:
        fname = "storm.yml"
        path  = os.path.join(self._root, "envs", fname)
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""

    def _do_pull(self):
        try:
            hash_before = self._env_hash()
            subprocess.run(
                ["git", "pull"],
                cwd=self._root, timeout=30,
                capture_output=True, check=True, **_HIDE_WINDOW,
            )
            hash_after  = self._env_hash()
            deps_changed = bool(hash_before) and hash_before != hash_after
            self.pull_done.emit(True, deps_changed)
        except Exception:
            self.pull_done.emit(False, False)

    def _find_conda(self) -> str | None:
        """Locate the conda executable."""
        # try common conda locations
        candidates = ["conda", "mamba"]
        for cmd in candidates:
            try:
                subprocess.run(
                    [cmd, "--version"],
                    capture_output=True, timeout=5, check=True, **_HIDE_WINDOW,
                )
                return cmd
            except Exception:
                continue
        return None

    def _do_conda_update(self):
        """Run conda env update in the background."""
        conda_cmd = self._find_conda()
        if not conda_cmd:
            self.conda_update_done.emit(False, "Conda not found in PATH")
            return

        env_file = os.path.join(self._root, "envs", "storm.yml")
        if not os.path.isfile(env_file):
            self.conda_update_done.emit(False, f"Environment file not found: {env_file}")
            return

        try:
            self._conda_proc = subprocess.Popen(
                [conda_cmd, "env", "update", "-f", env_file, "--prune"],
                cwd=self._root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                **_HIDE_WINDOW,
            )
            try:
                stdout, stderr = self._conda_proc.communicate(timeout=300)
            except subprocess.TimeoutExpired:
                self._conda_proc.kill()
                self._conda_proc.communicate()
                self.conda_update_done.emit(False, "Conda update timed out after 5 minutes")
                return
            if self._conda_proc.returncode == 0:
                self.conda_update_done.emit(True, "")
            elif self._conda_proc.returncode < 0:
                self.conda_update_done.emit(False, "Conda update was canceled")
            else:
                error_msg = (stderr.strip() or stdout.strip() or "Unknown error")[:500]
                self.conda_update_done.emit(False, error_msg)
        except Exception as e:
            self.conda_update_done.emit(False, str(e))
        finally:
            self._conda_proc = None
