"""Trusted GitHub persistence; credentials never enter source-build containers or tools."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from urllib.parse import quote

OPEN_SOURCE_LICENSES = {
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "GPL-2.0", "GPL-3.0",
    "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later",
    "LGPL-2.1", "LGPL-3.0", "AGPL-3.0", "MPL-2.0", "ISC", "Zlib",
    "Unlicense", "CC0-1.0", "BSL-1.0",
}
STATE_PATH = ".quiver-agent/knulli/state.json"
RECIPE_PATH = ".quiver-agent/knulli/build-recipe.txt"


def validate_source_edit(root, relative):
    parts = Path(relative).parts
    if (not parts or Path(relative).is_absolute() or
            any(part.startswith(".") for part in parts) or
            Path(relative).name.upper().startswith(("LICENSE", "COPYING", "COPYRIGHT", "NOTICE"))):
        raise ValueError("Source edits cannot change hidden/configuration paths or license notices.")
    path = root / relative
    current = root
    for part in parts:
        current /= part
        if current.is_symlink():
            raise ValueError("Source edits cannot traverse symlinks.")
        if current.is_dir() and (current / ".git").exists():
            raise ValueError("Submodule source edits require a separate fork; they cannot be checkpointed in the parent.")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Source edit escapes the checkout or targets a symlink.")
    return path


class GitHubApi:
    def __init__(self, token):
        if not token:
            raise RuntimeError("QUIVER_FORK_TOKEN is required to create and update build forks.")
        self.token = token

    def __call__(self, method, path, body=None, missing_ok=False):
        command = ["gh", "api", "--method", method, path]
        if body is not None:
            command += ["--input", "-"]
        result = subprocess.run(
            command, input=json.dumps(body) if body is not None else None,
            text=True, capture_output=True, timeout=60,
            env={**os.environ, "GH_TOKEN": self.token},
        )
        if result.returncode:
            if missing_ok and "(HTTP 404)" in result.stderr:
                return None
            raise RuntimeError(f"GitHub {method} {path} failed: {result.stderr.strip()}")
        return json.loads(result.stdout) if result.stdout.strip() else None


class ForkStore:
    def __init__(self, source, upstream, source_ref, profile, api):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", upstream):
            raise ValueError("Invalid upstream repository.")
        if not re.fullmatch(r"[0-9a-f]{40}", source_ref):
            raise ValueError("A pinned upstream commit is required.")
        self.source = source.resolve()
        self.upstream = upstream
        self.source_ref = source_ref
        self.profile_hash = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
        self.branch = f"quiver/knulli/{source_ref[:16]}-{self.profile_hash[:16]}"
        self.api = api
        self.repository = None
        self.head = None
        self.state = None

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.source), *args],
                                text=True, capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError(f"Git checkpoint operation failed: {result.stderr.strip()}")
        return result.stdout

    def ensure_fork(self):
        upstream = self.api("GET", f"repos/{self.upstream}")
        self.upstream = upstream["full_name"]
        license_id = (upstream.get("license") or {}).get("spdx_id")
        if upstream.get("private") or license_id not in OPEN_SOURCE_LICENSES:
            raise RuntimeError("Automatic forks require a public repository with a recognized open-source license.")
        owner = self.api("GET", "user")["login"]
        if self.upstream.split("/")[0].lower() == owner.lower():
            raise RuntimeError("Refusing to use the source owner's repository as a build fork.")
        self.repository = f"{owner}/{upstream['name']}"
        fork = self.api("GET", f"repos/{self.repository}", missing_ok=True)
        created = fork is None
        if created:
            self.api("POST", f"repos/{self.upstream}/forks", {"default_branch_only": False})
            for _ in range(15):
                fork = self.api("GET", f"repos/{self.repository}", missing_ok=True)
                if fork is not None:
                    break
                time.sleep(2)
            if fork is None:
                raise RuntimeError("GitHub has not finished creating the fork. Retry later.")
        network_id = (upstream.get("source") or upstream)["id"]
        fork_network_id = (fork.get("source") or fork.get("parent") or {}).get("id")
        if not fork.get("fork") or fork_network_id != network_id:
            raise RuntimeError(f"{self.repository} already exists and is not a fork of the requested project.")
        if created:
            self.api("PUT", f"repos/{self.repository}/actions/permissions", {"enabled": False})
        self.require_actions_disabled()
        ref = self.api("GET", f"repos/{self.repository}/git/ref/heads/{self.branch}", missing_ok=True)
        if ref is None:
            if (self.source / ".quiver-agent").exists():
                raise RuntimeError("Upstream already uses the reserved .quiver-agent checkpoint directory.")
            ref = self.api("POST", f"repos/{self.repository}/git/refs",
                           {"ref": "refs/heads/" + self.branch, "sha": self.source_ref})
        self.head = ref["object"]["sha"]
        state_file = self.api("GET",
            f"repos/{self.repository}/contents/{STATE_PATH}?ref={self.head}", missing_ok=True)
        if state_file is not None:
            self.state = json.loads(base64.b64decode(state_file["content"]))
            if (self.state.get("schema") != 1 or type(self.state.get("round")) is not int or
                    self.state["round"] < 0 or
                    self.state.get("upstream") != self.upstream or self.state.get("source_ref") != self.source_ref or
                    self.state.get("profile_hash") != self.profile_hash):
                raise RuntimeError("Existing fork checkpoint does not match the requested source and device.")
        elif self.head != self.source_ref:
            raise RuntimeError("Existing build branch has no matching checkpoint; refusing to overwrite it.")
        return self.state

    def require_actions_disabled(self):
        if self.api("GET", f"repos/{self.repository}/actions/permissions")["enabled"]:
            raise RuntimeError(f"Disable Actions on {self.repository} before automatic source-fix pushes.")

    def restore(self):
        if self.head != self.git("rev-parse", "HEAD").strip():
            if self.git("status", "--porcelain").strip():
                raise RuntimeError("Refusing to replace a dirty source checkout while resuming a fork.")
            self.git("fetch", "--no-tags", "--depth=1", f"https://github.com/{self.repository}.git", self.head)
            self.git("switch", "--detach", self.head)
        return self.state

    def checkpoint(self, engine, status):
        self.require_actions_disabled()
        current = self.api("GET", f"repos/{self.repository}/git/ref/heads/{self.branch}")
        if current["object"]["sha"] != self.head:
            raise RuntimeError("The fork branch changed concurrently; refusing to overwrite another build's progress.")
        changes = set(self.git("diff", "--no-ext-diff", "--no-renames", "--name-only", "-z", "HEAD").split("\0"))
        changes.update(self.git("ls-files", "--others", "--exclude-standard", "-z").split("\0"))
        changes.update(getattr(engine, "edited_paths", set()))
        changes.discard("")
        if len(changes) > 64:
            raise RuntimeError("A checkpoint cannot push more than 64 source files.")
        tree = []
        total = 0
        for relative in sorted(changes):
            path = validate_source_edit(self.source, relative)
            if not path.exists():
                tree.append({"path": relative, "mode": "100644", "type": "blob", "sha": None})
                continue
            if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > 2_000_000:
                raise RuntimeError("Checkpoint source files must be regular files smaller than 2 MB.")
            content = path.read_bytes()
            total += len(content)
            if total > 8_000_000:
                raise RuntimeError("Checkpoint source changes exceed 8 MB.")
            blob = self.api("POST", f"repos/{self.repository}/git/blobs",
                            {"encoding": "base64", "content": base64.b64encode(content).decode()})
            tree.append({"path": relative, "mode": "100755" if path.stat().st_mode & 0o111 else "100644",
                         "type": "blob", "sha": blob["sha"]})
        state = {
            "schema": 1, "upstream": self.upstream, "source_ref": self.source_ref,
            "profile_hash": self.profile_hash, "status": status, "runtime_verified": False,
            "round": engine.round_number, "attempts": len(engine.attempts), "tool_calls": engine.calls,
            "last_build": engine.last_outcome, "last_tool_error": engine.last_tool_error,
            "summary": engine.last_summary, "previous_commit": self.head,
        }
        metadata = {STATE_PATH: json.dumps(state, indent=2)}
        if engine.attempts:
            metadata[RECIPE_PATH] = (engine.attempts[-1] / "recipe" / "build.sh").read_text()
        for path, content in metadata.items():
            tree.append({"path": path, "mode": "100644", "type": "blob", "content": content})
        base = self.api("GET", f"repos/{self.repository}/git/commits/{self.head}")
        new_tree = self.api("POST", f"repos/{self.repository}/git/trees", {"base_tree": base["tree"]["sha"], "tree": tree})
        commit = self.api("POST", f"repos/{self.repository}/git/commits", {
            "message": f"Checkpoint Knulli agent round {engine.round_number}: {status}\n\n"
                       "Controller/runtime behavior remains unverified.\n\n"
                       "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
            "tree": new_tree["sha"], "parents": [self.head],
        })
        self.api("PATCH", f"repos/{self.repository}/git/refs/heads/{self.branch}",
                 {"sha": commit["sha"], "force": False})
        self.head = commit["sha"]
        self.state = state
        self.git("fetch", "--no-tags", "--depth=1", f"https://github.com/{self.repository}.git", self.head)
        # Synchronize Git metadata to exactly the committed tree without overwriting source edits.
        self.git("read-tree", self.head)
        self.git("update-ref", "HEAD", self.head)
        for path, content in metadata.items():
            destination = self.source / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content)
        location = {"repository": self.repository, "branch": self.branch, "commit": self.head,
                    "url": f"https://github.com/{self.repository}/tree/{quote(self.branch, safe='/')}"}
        engine.record("fork_checkpoint", status=status, **location)
        print(f"[fork] Saved {status} checkpoint: {location['url']} ({self.head})", flush=True)
        return location
