#!/usr/bin/env python3
"""Read-only release preflight; optional live checks never send or redeem."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

from configuration import load_config, runtime_arguments
from controller import read_account, validate_config
from native_bridge import NativeBridge, verify_bridge
from storage import read_private
from transport import socket_identity


def preflight(config, state_dir, *, check_live=False):
    validate_config(config, activate=True)
    root = Path(state_dir)
    lock = Path(config['sharedLock']).lstat()
    if not stat.S_ISREG(lock.st_mode) or lock.st_uid != os.getuid():
        raise ValueError('existing_owned_singleton_lock_required')
    # Hash and summarize; never initialize, replace, restore or reconcile ledgers.
    preserved = {}
    for path in sorted(root.glob('*.json')):
        if path.name not in ('main.json', 'daybreak.json', 'main-selection.json') and not path.name.startswith(('reset-', 'release-', 'closed-')):
            continue
        read_private(path)
        preserved[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = {'status': 'local_checks_passed', 'preservedLedgerHashes': preserved,
              'policy': config['policy'], 'liveChecks': 'not_requested'}
    if check_live:
        for account in config['accounts']:
            read_account(account, node=config['nodeBinary'])
        pipe = os.environ.get('CODEX_APP_TOOLS_PIPE_PATH')
        socket_identity(pipe)
        bridge = NativeBridge(config, pipe)
        try: verify_bridge(bridge, config['foremanThreadId'], config['parentThreadId'])
        finally: bridge.close()
        report['liveChecks'] = 'both_accounts_and_exact_destinations_verified'
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); runtime_arguments(parser)
    parser.add_argument('--check-live', action='store_true')
    args = parser.parse_args()
    print(json.dumps(preflight(load_config(args.config, args.state_dir), args.state_dir,
                               check_live=args.check_live), indent=2))
