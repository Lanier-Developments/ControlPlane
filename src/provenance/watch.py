"""Keep the index serving what the source contains, continuously.

Ingest reconciliation (ingest.py) closes the gap at ingest time; this closes it in
time. A polling loop hashes the corpus and re-ingests whenever the digest no longer
matches the last recorded run — which makes staleness a stated bound rather than
"until someone remembers": detection within one interval, serving within one
interval plus one incremental ingest.

Polling rather than filesystem events, on purpose: stdlib only, identical on every
platform and filesystem (events are unreliable on network mounts, where a corpus
often lives), and the interval is an explicit number someone chose, visible in the
process list, not an OS queue that can silently drop.

A corpus file that is invalid mid-edit (a torn write, missing frontmatter) skips
that pass with a message rather than killing the watcher; the next pass picks the
finished file up.
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import settings
from .ingest import corpus_digest, last_ingested, load_corpus, run_ingest


def _stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def check_once() -> bool:
    """One pass: re-ingest if the disk and the last run disagree. True if ingested."""
    docs = load_corpus(Path(settings.corpus_dir))
    disk = corpus_digest(docs)
    last = last_ingested()
    if last is not None and last[0] == disk:
        return False
    print(f"[{_stamp()}] corpus changed; ingesting")
    s = run_ingest()
    line = (f"[{_stamp()}] {s['documents']} documents: {s['embedded']} embedded, "
            f"{s['unchanged']} unchanged")
    if s["removed"]:
        line += "; removed " + ", ".join(f"{d} v{v}" for d, v in s["removed"])
    print(line, flush=True)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Watch the corpus; re-ingest on change")
    ap.add_argument("--interval", type=float, default=settings.watch_interval_seconds,
                    help="seconds between polls; the stated bound on detection delay")
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    args = ap.parse_args()

    if args.once:
        check_once()
        return 0

    print(f"Watching {settings.corpus_dir} every {args.interval:g}s (ctrl-c to stop)", flush=True)
    while True:
        try:
            check_once()
        except Exception as exc:  # a torn write must not kill the watcher
            print(f"[{_stamp()}] pass skipped: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
