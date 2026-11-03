#!/usr/bin/env python3
"""Read-only release preflight; optional live checks never send or redeem."""
import argparse
import fcntl
import hashlib
import json
import os
import uuid
from pathlib import Path

from configuration import load_config, runtime_arguments
from controller import open_singleton_lock, read_account, validate_config, check_account_custody, reset_evidence, check_main_selection
from native_bridge import NativeBridge, verify_bridge
from storage import read_private
from transport import socket_identity
from activation import reset_operation_lock


def preflight(config, state_dir, *, check_live=False):
    validate_config(config, activate=True)
    root = Path(state_dir)
    # The observer owns this lock for its lifetime, including maintenance and
    # observe-only. A stopped cutover is the supported snapshot boundary.
    with open_singleton_lock(config['sharedLock']) as singleton:
        try:
            fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('observer_stop_required_for_preflight') from None
        # Hash and summarize under an existing lock; never create or reconcile state.
        preserved = {}
        with reset_operation_lock(root, blocking=False, create=False):
            for name in ('main', 'daybreak'):
                check_account_custody(root / f'{name}.json')
            check_main_selection(root)
            if (root / 'daybreak.json').exists():
                reset_evidence(root, read_private(root / 'daybreak.json'))
            for marker_path in root.glob('reset-custody-*.json'):
                episode = marker_path.name.removeprefix('reset-custody-').removesuffix('.json')
                try:
                    if str(uuid.UUID(episode)) != episode:
                        raise ValueError
                    marker = read_private(marker_path)
                    record = read_private(root / f'reset-{episode}.json')
                    request = record.get('request') if isinstance(record, dict) else None
                    if (not isinstance(marker, dict) or marker.get('episodeId') != episode
                            or not isinstance(request, dict)
                            or any(request.get(k) != marker.get(k) for k in ('episodeId', 'accountId', 'idempotencyKey'))):
                        raise ValueError
                except (ValueError, TypeError, AttributeError):
                    raise ValueError('reset_reconciliation_required') from None
            for record_path in root.glob('reset-*.json'):
                if record_path.name.startswith('reset-custody-'):
                    continue
                episode = record_path.name.removeprefix('reset-').removesuffix('.json')
                try:
                    if str(uuid.UUID(episode)) != episode or not (root / f'reset-custody-{episode}.json').is_file():
                        raise ValueError
                except ValueError:
                    raise ValueError('reset_reconciliation_required') from None
            for path in sorted(root.glob('*.json')):
                if path.name not in ('main.json', 'daybreak.json', 'main-selection.json') and not path.name.startswith(('reset-', 'release-', 'closed-', 'account-custody-')):
                    continue
                retained = read_private(path)
                if (path.name.startswith('closed-') and isinstance(retained, dict)
                        and retained.get('resetEvidence') is not None):
                    reset_evidence(root, {'accountId': retained.get('accountId'), 'episode': retained,
                                          'resetEvidence': retained['resetEvidence']})
                preserved[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        report = {'status': 'local_checks_passed', 'preservedLedgerHashes': preserved,
                  'policy': config['policy'], 'liveChecks': 'not_requested'}
        if check_live:
            for account in config['accounts']:
                read_account(account, node=config['nodeBinary'])
            pipe = os.environ.get('CODEX_APP_TOOLS_PIPE_PATH')
            socket_identity(pipe)
            bridge = NativeBridge(config, pipe)
            try:
                if config.get('incidentRoutingEnabled') is True:
                    bindings = config['roleBindings']
                    verify_bridge(bridge, config['foremanThreadId'], config['parentThreadId'],
                                  dispatch_binding=bindings['foreman'], concierge=config['conciergeThreadId'],
                                  concierge_binding=bindings['concierge'], owner_binding=bindings['repairOwner'])
                else:
                    verify_bridge(bridge, config['foremanThreadId'], config['parentThreadId'])
            finally: bridge.close()
            report['liveChecks'] = ('accounts_and_bound_recipients_readable' if config.get('incidentRoutingEnabled') is True
                                    else 'both_accounts_and_exact_destinations_verified')
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); runtime_arguments(parser)
    parser.add_argument('--check-live', action='store_true')
    args = parser.parse_args()
    print(json.dumps(preflight(load_config(args.config, args.state_dir), args.state_dir,
                               check_live=args.check_live), indent=2))
