"""Publish wiki/ (pages and images/) to the GitHub wiki.

The wiki's source of truth is the wiki/ folder in this repo; the GitHub wiki is a copy.
Pages edited on GitHub are overwritten, and pages that no longer exist in wiki/ are removed.

    python tools/publish_wiki.py --dry-run   # clone, sync, show the diff, push nothing
    python tools/publish_wiki.py             # ... and commit + push

Needs git with push access to the repo (the same login as for the code). The wiki repo only
exists once a first page has been created on GitHub in the browser.
"""
import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
WIKI_SRC = ROOT / "wiki"
IMAGES_SRC = WIKI_SRC / "images"


def git(*args, cwd, capture=False):
    r = subprocess.run(["git", *args], cwd=cwd, check=True, text=True,
                       stdout=subprocess.PIPE if capture else None)
    return r.stdout if capture else None


def wiki_url():
    origin = git("remote", "get-url", "origin", cwd=ROOT, capture=True).strip()
    return origin.removesuffix(".git") + ".wiki.git"


def sync(dest: pathlib.Path):
    """Make dest's pages and images/ an exact copy of the sources."""
    for old in dest.glob("*.md"):
        old.unlink()
    shutil.rmtree(dest / "images", ignore_errors=True)
    for page in WIKI_SRC.glob("*.md"):
        shutil.copy2(page, dest / page.name)
    (dest / "images").mkdir()
    for img in IMAGES_SRC.glob("*.png"):
        shutil.copy2(img, dest / "images" / img.name)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="show the changes, don't push")
    args = ap.parse_args()

    commit = git("rev-parse", "--short", "HEAD", cwd=ROOT, capture=True).strip()
    with tempfile.TemporaryDirectory() as tmp:
        dest = pathlib.Path(tmp) / "wiki"
        url = wiki_url()
        try:
            git("clone", "-q", "-c", "core.autocrlf=false", url, str(dest), cwd=tmp)
        except subprocess.CalledProcessError:
            sys.exit(f"Could not clone {url}. Create the first wiki page on GitHub first.")
        sync(dest)
        git("add", "-A", cwd=dest)
        status = git("status", "--short", cwd=dest, capture=True)
        if not status.strip():
            print("Wiki is up to date.")
            return
        print(status, end="")
        if args.dry_run:
            print("(dry run: nothing pushed)")
            return
        git("commit", "-q", "-m", f"Publish wiki from {commit}", cwd=dest)
        git("push", "-q", cwd=dest)
        print(f"Published wiki from {commit}.")


if __name__ == "__main__":
    main()
