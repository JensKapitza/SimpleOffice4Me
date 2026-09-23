"""Read-only Git integration for project workspaces.

SimpleOffice never becomes a source-control implementation. Code views exist
only for a configured, allowed local path that is already a real Git working
tree. All repository commands use argv execution without a shell.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any


MAX_FILE_BYTES = 512 * 1024
MAX_GIT_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_TREE_ENTRIES = 500
MAX_COMMITS = 200
_REF = re.compile(r"^(?:HEAD|[0-9a-fA-F]{7,40})$")


class ProjectGitError(RuntimeError):
    pass


class ProjectGitService:
    def __init__(self, root: str | Path, project: dict[str, Any]):
        self.root = Path(root).expanduser().resolve()
        self.project = project
        self.git = shutil.which("git")

    def available(self) -> bool:
        if not self.git or not str(self.project.get("repository_path") or "").strip():
            return False
        try:
            self.repository_path()
            return True
        except (OSError, ValueError, ProjectGitError, subprocess.TimeoutExpired):
            return False

    def repository_path(self) -> Path:
        if not self.git:
            raise ProjectGitError("Git ist auf diesem Server nicht installiert")
        configured = str(self.project.get("repository_path") or "").strip()
        if not configured or "\x00" in configured or len(configured) > 2048:
            raise ValueError("Projekt-Repository ist nicht konfiguriert")
        supplied = Path(configured).expanduser()
        candidate = supplied if supplied.is_absolute() else self.root / supplied
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ProjectGitError("Projekt-Repository ist nicht erreichbar") from exc
        if not resolved.is_dir():
            raise ProjectGitError("Projekt-Repository ist kein Ordner")
        if not self._inside_allowed_root(resolved):
            raise ProjectGitError("Projekt-Repository liegt außerhalb der freigegebenen Repo-Roots")

        top = self._run_at(resolved, ["rev-parse", "--show-toplevel"], maximum=16 * 1024)
        try:
            top_path = Path(top.decode("utf-8").strip()).resolve(strict=True)
        except (OSError, UnicodeError) as exc:
            raise ProjectGitError("Git-Arbeitsverzeichnis konnte nicht bestimmt werden") from exc
        if top_path != resolved:
            raise ProjectGitError("Repository-Pfad muss auf die Wurzel des Git-Arbeitsverzeichnisses zeigen")
        git_dir_raw = self._run_at(
            resolved,
            ["rev-parse", "--absolute-git-dir"],
            maximum=16 * 1024,
        )
        try:
            git_dir = Path(git_dir_raw.decode("utf-8").strip()).resolve(strict=True)
        except (OSError, UnicodeError) as exc:
            raise ProjectGitError("Git-Metadatenverzeichnis konnte nicht bestimmt werden") from exc
        if not self._inside_allowed_root(git_dir):
            raise ProjectGitError("Git-Metadaten liegen außerhalb der freigegebenen Repo-Roots")
        return resolved

    def summary(self) -> dict[str, Any]:
        repo = self.repository_path()
        branch = self._run_at(
            repo,
            ["symbolic-ref", "--short", "-q", "HEAD"],
            maximum=4096,
            accepted_returncodes=(0, 1),
        ).decode("utf-8", "replace").strip() or "HEAD"
        head = self._run_at(
            repo,
            ["rev-parse", "--verify", "--quiet", "HEAD"],
            maximum=4096,
            accepted_returncodes=(0, 1),
        ).decode("ascii", "replace").strip()
        status = self._run_at(
            repo,
            ["status", "--porcelain=v1", "--branch", "--untracked-files=normal"],
            maximum=256 * 1024,
        ).decode("utf-8", "replace").splitlines()
        return {
            "available": True,
            "branch": branch,
            "head": head,
            "has_head": bool(head),
            "dirty": any(line and not line.startswith("##") for line in status),
            "status_lines": status[:100],
            "commits": self.commits(limit=30) if head else [],
        }

    def commits(self, *, limit: int = 50) -> list[dict[str, str]]:
        repo = self.repository_path()
        bounded = max(1, min(int(limit), MAX_COMMITS))
        fmt = "%H%x1f%h%x1f%an%x1f%aI%x1f%s%x1e"
        raw = self._run_at(
            repo,
            ["log", f"-n{bounded}", f"--pretty=format:{fmt}"],
            maximum=MAX_GIT_OUTPUT_BYTES,
        ).decode("utf-8", "replace")
        rows = []
        for record in raw.split("\x1e"):
            record = record.strip()
            if not record:
                continue
            fields = record.split("\x1f", 4)
            if len(fields) != 5:
                continue
            rows.append({
                "sha": fields[0],
                "short_sha": fields[1],
                "author": fields[2],
                "authored_at": fields[3],
                "subject": fields[4],
            })
        return rows

    def commits_for_issue(self, issue_number: int) -> list[dict[str, str]]:
        if isinstance(issue_number, bool) or int(issue_number) < 1:
            raise ValueError("invalid issue number")
        marker = re.compile(rf"(?<![A-Za-z0-9])#{int(issue_number)}(?!\d)")
        return [
            row for row in self.commits(limit=MAX_COMMITS)
            if marker.search(row["subject"])
        ][:30]

    def tree(self, *, ref: str = "HEAD", path: str = "") -> list[dict[str, str]]:
        checked_ref = self._ref(ref)
        checked_path = self._relative_path(path, allow_empty=True)
        spec = checked_ref if not checked_path else f"{checked_ref}:{checked_path}"
        raw = self._run(
            ["ls-tree", "-z", spec],
            maximum=MAX_GIT_OUTPUT_BYTES,
        )
        entries = []
        for record in raw.split(b"\x00"):
            if not record:
                continue
            try:
                header, name = record.split(b"\t", 1)
                mode, kind, sha = header.decode("ascii").split(" ", 2)
                decoded_name = name.decode("utf-8", "replace")
            except (ValueError, UnicodeError):
                continue
            entries.append({
                "mode": mode,
                "type": kind,
                "sha": sha,
                "name": decoded_name,
                "path": f"{checked_path}/{decoded_name}".strip("/"),
            })
            if len(entries) >= MAX_TREE_ENTRIES:
                break
        return entries

    def text_file(self, path: str, *, ref: str = "HEAD") -> dict[str, str]:
        checked_ref = self._ref(ref)
        checked_path = self._relative_path(path, allow_empty=False)
        spec = f"{checked_ref}:{checked_path}"
        size_raw = self._run(["cat-file", "-s", spec], maximum=4096)
        try:
            size = int(size_raw.decode("ascii").strip())
        except (UnicodeError, ValueError) as exc:
            raise ProjectGitError("Git-Dateigröße ist ungültig") from exc
        if size < 0 or size > MAX_FILE_BYTES:
            raise ProjectGitError(f"Datei ist größer als {MAX_FILE_BYTES // 1024} KiB")
        payload = self._run(["cat-file", "blob", spec], maximum=MAX_FILE_BYTES + 1)
        if b"\x00" in payload[:8192]:
            raise ProjectGitError("Binärdateien werden in der Codeansicht nicht dargestellt")
        try:
            text = payload.decode("utf-8")
        except UnicodeError as exc:
            raise ProjectGitError("Datei ist nicht UTF-8 kodiert") from exc
        return {"path": checked_path, "ref": checked_ref, "text": text, "size": str(size)}

    def _run(self, args: list[str], *, maximum: int) -> bytes:
        return self._run_at(self.repository_path(), args, maximum=maximum)

    def _run_at(
        self,
        repo: Path,
        args: list[str],
        *,
        maximum: int,
        accepted_returncodes: tuple[int, ...] = (0,),
    ) -> bytes:
        if not self.git:
            raise ProjectGitError("Git ist nicht installiert")
        env = {
            key: os.environ[key]
            for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE")
            if key in os.environ
        }
        env.update({
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "LC_ALL": "C",
        })
        process = subprocess.Popen(
            [
                self.git,
                "-c", "color.ui=false",
                "-c", "core.fsmonitor=false",
                "-c", "core.untrackedCache=false",
                "-c", "diff.external=",
                *args,
            ],
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_parts: list[bytes] = []
        stderr_parts: list[bytes] = []
        stdout_overflow = threading.Event()
        stderr_overflow = threading.Event()
        stdout_thread = threading.Thread(
            target=self._drain_bounded,
            args=(process.stdout, maximum, stdout_parts, stdout_overflow),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._drain_bounded,
            args=(process.stderr, 64 * 1024, stderr_parts, stderr_overflow),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        try:
            returncode = process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        finally:
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)

        stdout = b"".join(stdout_parts)
        stderr = b"".join(stderr_parts)
        if stdout_overflow.is_set():
            raise ProjectGitError("Git-Ausgabe überschreitet das Sicherheitslimit")
        if returncode not in accepted_returncodes:
            message = stderr.decode("utf-8", "replace").strip().splitlines()
            detail = message[-1][:300] if message else f"Git exited with {returncode}"
            raise ProjectGitError(detail)
        return stdout

    @staticmethod
    def _drain_bounded(
        stream: Any,
        maximum: int,
        parts: list[bytes],
        overflow: threading.Event,
    ) -> None:
        """Drain a child pipe while retaining at most maximum+1 bytes."""

        if stream is None:
            return
        retained = 0
        try:
            while True:
                block = stream.read(64 * 1024)
                if not block:
                    break
                if retained <= maximum:
                    keep = block[: max(0, maximum + 1 - retained)]
                    if keep:
                        parts.append(keep)
                        retained += len(keep)
                    if retained > maximum:
                        overflow.set()
        finally:
            stream.close()

    def _inside_allowed_root(self, candidate: Path) -> bool:
        for root in self._allowed_roots():
            if candidate == root or root in candidate.parents:
                return True
        return False

    def _allowed_roots(self) -> list[Path]:
        roots = [self.root]
        configured = os.environ.get("SIMPLEOFFICE_PROJECT_REPO_ROOTS", "")
        for item in configured.split(os.pathsep):
            text = item.strip()
            if not text:
                continue
            try:
                root = Path(text).expanduser().resolve(strict=True)
            except OSError:
                continue
            if root.is_dir() and root not in roots:
                roots.append(root)
        return roots

    @staticmethod
    def _ref(value: str) -> str:
        checked = str(value or "HEAD").strip()
        if not _REF.fullmatch(checked):
            raise ValueError("Git-Referenz muss HEAD oder eine Commit-SHA sein")
        return checked

    @staticmethod
    def _relative_path(value: str, *, allow_empty: bool) -> str:
        text = str(value or "").strip().replace("\\", "/")
        if not text and allow_empty:
            return ""
        if (
            not text
            or text.startswith("/")
            or ":" in text
            or "\x00" in text
            or len(text) > 1000
            or any(part in {"", ".", ".."} for part in text.split("/"))
        ):
            raise ValueError("ungültiger Git-Pfad")
        return text
