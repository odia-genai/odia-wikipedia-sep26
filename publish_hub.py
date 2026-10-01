#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["huggingface_hub>=1.0"]
# ///
"""Publish this repository's dataset to Hugging Face: the files git tracks at HEAD, without edaapp/.

  uv run publish_hub.py --dry-run                     # what would be uploaded and deleted
  uv run publish_hub.py                               # upload (log in first: `hf auth login`, or HF_TOKEN)
  uv run publish_hub.py --token-file path/to/token    # or read the token from a file

It refuses to run with uncommitted changes to tracked files, names the git commit in the Hub commit's
message, deletes Hub files git no longer tracks, and checks every file afterwards: its size, and the
SHA-256 of large files or the git blob id of the others. GitHub holds the history; the Hub gets one commit
per publish.
"""

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = "fastpixels/odia-wikipedia-sep26"
GITHUB = "https://github.com/odia-genai/odia-wikipedia-sep26"
LEFT_OUT = ("edaapp/",)  # the review app lives on GitHub only


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--token-file", type=Path, help="a file holding the Hugging Face token (default: your login)")
    args = ap.parse_args()
    if git("status", "--porcelain", "--untracked-files=no").strip():
        sys.exit("uncommitted changes to tracked files: commit them first, so the Hub matches a commit")
    sha = git("rev-parse", "HEAD").strip()
    files = [f for f in git("ls-files", "-z").split("\0") if f and not f.startswith(LEFT_OUT)]

    from huggingface_hub import CommitOperationDelete, HfApi
    from huggingface_hub.hf_api import RepoFile

    api = HfApi(token=args.token_file.read_text().strip() if args.token_file else None)
    remote = {f.path for f in api.list_repo_tree(args.repo, repo_type="dataset", recursive=True) if isinstance(f, RepoFile)}
    stale = sorted(remote - set(files) - {".gitattributes"})
    print(f"{args.repo}: {len(files)} files from commit {sha[:7]}; {len(stale)} to delete on the Hub", *stale, sep="\n  ")
    if args.dry_run:
        return
    title = f"odia-wikipedia-sep26 at {sha[:7]} ({GITHUB.removeprefix('https://')})"
    info = api.upload_folder(repo_id=args.repo, repo_type="dataset", folder_path=str(ROOT), allow_patterns=files,
                             commit_message=title,
                             commit_description=f"All {len(files)} files of commit {sha} of {GITHUB}, without edaapp/.")
    print("uploaded:", getattr(info, "oid", info))
    if stale:
        api.create_commit(repo_id=args.repo, repo_type="dataset", commit_message=f"{title}: remove files git no longer tracks",
                          operations=[CommitOperationDelete(path_in_repo=p) for p in stale])
        print(f"deleted {len(stale)} files")
    hub = {f.path: f for f in api.list_repo_tree(args.repo, repo_type="dataset", recursive=True) if isinstance(f, RepoFile)}
    bad = []
    for p in files:
        f, local = hub.get(p), ROOT / p
        if f is None or f.size != local.stat().st_size:
            bad.append(p)
        elif f.lfs:
            bad += [p] if f.lfs.sha256 != hashlib.sha256(local.read_bytes()).hexdigest() else []
        elif f.blob_id != git("hash-object", p).strip():
            bad.append(p)
    extra = sorted(set(hub) - set(files) - {".gitattributes"})
    print(f"checked {len(files)} files on the Hub: {'all match' if not bad else f'{len(bad)} differ: {bad[:5]}'}"
          + (f"; extra: {extra}" if extra else ""))
    sys.exit(1 if bad or extra else 0)


if __name__ == "__main__":
    main()
