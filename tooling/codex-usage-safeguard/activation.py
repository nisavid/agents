#!/usr/bin/env python3
"""Persist delivery maintenance across service restarts; never change access."""
import argparse
from contextlib import contextmanager
import fcntl
import os
import stat
import uuid
import hashlib
import json
from pathlib import Path
import time

from storage import read_private, save, private_directory


def configuration_binding(config):
    fields = {key: value for key, value in config.items() if not key.startswith('_')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


@contextmanager
def decision_lock(state_dir):
    """Serialize maintenance decisions with intent, send and its receipt."""
    root = private_directory(state_dir, create=False)
    fd = os.open(root / 'activation.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('private_owned_activation_lock_required')
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def delivery_status(config, state_dir):
    """Missing, malformed or obsolete activation is maintenance, never auto-arm."""
    try:
        if config.get('_configPath'):
            current = read_private(config['_configPath'])
            if not isinstance(current, dict) or configuration_binding(current) != configuration_binding(config):
                return {'mode': 'maintenance', 'reason': 'activation_configuration_changed'}
        value = read_private(Path(state_dir) / 'activation.json', {})
        if not isinstance(value, dict) or value.get('mode') not in ('active', 'maintenance'):
            return {'mode': 'maintenance', 'reason': 'activation_required'}
        if value['mode'] == 'maintenance':
            return {'mode': 'maintenance', 'reason': 'operator_maintenance', 'reference': value.get('reference')}
        if value.get('binding') != configuration_binding(config):
            return {'mode': 'maintenance', 'reason': 'activation_configuration_changed'}
        if not isinstance(value.get('reference'), str) or not value['reference'].strip():
            return {'mode': 'maintenance', 'reason': 'activation_reference_missing'}
        return {'mode': 'active', 'reference': value['reference']}
    except (OSError, ValueError):
        return {'mode': 'maintenance', 'reason': 'activation_record_unreadable'}


def set_mode(config, state_dir, mode, reference, *, live_preflight=None):
    if mode not in ('active', 'maintenance') or not isinstance(reference, str) or not reference.strip():
        raise ValueError('mode_and_verified_operator_reference_required')
    path = Path(state_dir) / 'activation.json'
    # Always persist maintenance first. A failed activation preflight must not
    # leave a former activation armed for the next boot.
    decision = {'mode': 'maintenance', 'reference': reference, 'at': time.time(), 'decisionId': str(uuid.uuid4())}
    with decision_lock(state_dir):
        save(path, decision)
    if mode == 'active':
        if live_preflight is None:
            from preflight import preflight
            live_preflight = preflight
        live_preflight(config, Path(state_dir), check_live=True)
        with decision_lock(state_dir):
            if read_private(path) != decision:
                raise RuntimeError('activation_superseded_by_newer_decision')
            save(path, {'mode': 'active', 'reference': reference, 'at': time.time(),
                        'decisionId': decision['decisionId'], 'binding': configuration_binding(config)})
    return delivery_status(config, state_dir)


if __name__ == '__main__':
    from configuration import load_config, runtime_arguments
    parser = argparse.ArgumentParser(description=__doc__)
    runtime_arguments(parser)
    parser.add_argument('--mode', choices=('maintenance', 'active'), required=True)
    parser.add_argument('--reference', required=True,
                        help='Verified operator decision; a supplied string is not itself approval')
    args = parser.parse_args()
    print(json.dumps(set_mode(load_config(args.config, args.state_dir), args.state_dir,
                              args.mode, args.reference), indent=2))
