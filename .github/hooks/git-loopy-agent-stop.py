#!/usr/bin/env python3
import os
import sys
directory = os.path.dirname(__file__)
roots = [os.path.join(os.environ.get("COPILOT_HOME") or os.path.expanduser("~/.copilot"), "skills"), os.path.join(directory, ".."), os.path.join(directory, "../..", "skills")]
scripts = next((os.path.join(root, "next/scripts") for root in roots if os.access(os.path.join(root, "next/scripts/chain.sh"), os.X_OK)), os.path.join(roots[-1], "next/scripts"))
if __name__ == "__main__":
    os.execv(os.path.join(scripts, "chain.sh"), ["chain.sh", "reenter"])
sys.path.insert(0, scripts)
from chain_ledger import acquire_lock, lock_is_stale, release_lock
