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


def bounded_private_json(path, limit):
    """Read one private regular JSON file without trusting its size to stay fixed."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > limit):
            raise ValueError('bounded_private_file_required')
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('bounded_private_file_required')
        try:
            return json.loads(data)
        except RecursionError:
            raise ValueError('bounded_private_file_invalid') from None


# This packet is evidence selected by a reviewed operator decision, not a grant.
ROUTING_WITNESSES = ('caller-provenance', 'recipient-acceptance', 'writer-commit',
                     'writer-restart', 'consumer-restart', 'serialization')


def evidence_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def source_binding():
    """Bind the package bytes, including its specification and locked dependencies."""
    root = Path(__file__).resolve().parent
    files = {}
    total = 0
    for path in sorted(root.iterdir()):
        if path.suffix not in ('.py', '.mjs', '.json', '.md'):
            continue
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('regular_release_source_required')
        if len(files) >= 1024:
            raise ValueError('release_source_too_large')
        digest = hashlib.sha256()
        size = 0
        with path.open('rb') as stream:
            while chunk := stream.read(65536):
                size += len(chunk)
                total += len(chunk)
                if size > 8 * 1024 * 1024 or total > 64 * 1024 * 1024:
                    raise ValueError('release_source_too_large')
                digest.update(chunk)
        files[path.name] = digest.hexdigest()
    return evidence_digest(files)


def _qualified_attempt(config, identity, attempt, target, host):
    if (not isinstance(identity, dict) or not isinstance(attempt, dict)
            or set(identity) != {'incident_id', 'sender', 'sender_host', 'recipient', 'recipient_host'}
            or set(attempt) != {'id', 'caller', 'caller_host', 'target', 'target_host', 'payload'}
            or not all(isinstance(value, str) and value.strip()
                       for value in list(identity.values()) + list(attempt.values()))):
        raise ValueError('incident_routing_not_ready')
    if (identity['sender'] != config['nativeContext']['threadId']
            or identity['sender_host'] != config['nativeContext']['host']
            or identity['recipient'] != target or identity['recipient_host'] != host
            or attempt['caller'] != identity['sender'] or attempt['caller_host'] != identity['sender_host']
            or attempt['target'] != target or attempt['target_host'] != host
            or identity['incident_id'] not in attempt['payload'] or attempt['id'] not in attempt['payload']):
        raise ValueError('incident_routing_not_ready')


def routing_readiness(config, state_dir, *, bridge=None):
    """Validate retained bindings; with a bridge, recheck native evidence without sends.

    Witness hashes prove canonical JSON contents, not execution or operator authentication.
    The activation command separately selects this packet's digest after review.
    """
    from incident_delivery import match_submission, verify_ack, reconcile_snapshot
    try:
        if Path(state_dir).resolve() != Path(config['stateDir']).resolve():
            raise ValueError
        packet = bounded_private_json(Path(state_dir) / 'routing-readiness.json', 256 * 1024)
        if (not isinstance(packet, dict) or set(packet) != {
                'version', 'configurationBinding', 'sourceBinding', 'parentHost', 'dispatch',
                'conciergeIncident', 'conciergeRecordDigest', 'artifacts'}
                or type(packet['version']) is not int or packet['version'] != 1
                or packet['configurationBinding'] != configuration_binding(config)
                or packet['sourceBinding'] != source_binding()
                or not isinstance(packet['parentHost'], str) or not packet['parentHost'].strip()
                or packet['parentHost'] != config.get('parentHostId')
                or not isinstance(packet['artifacts'], dict)
                or set(packet['artifacts']) != set(ROUTING_WITNESSES)):
            raise ValueError
        witnesses = private_directory(Path(state_dir) / 'routing-evidence', create=False)
        for name in ROUTING_WITNESSES:
            witness = bounded_private_json(witnesses / (name + '.json'), 64 * 1024)
            if not isinstance(witness, dict) or evidence_digest(witness) != packet['artifacts'][name]:
                raise ValueError
        dispatch = packet['dispatch']
        if not isinstance(dispatch, dict) or set(dispatch) != {'identity', 'attempt', 'submission', 'receipt'}:
            raise ValueError
        bindings = config['roleBindings']
        _qualified_attempt(config, dispatch['identity'], dispatch['attempt'],
                           config['foremanThreadId'], bindings['foreman']['host_id'])
        for key, fields in (('submission', {'host', 'turn', 'message'}), ('receipt', {'turn', 'message'})):
            reference = dispatch[key]
            if (not isinstance(reference, dict) or set(reference) != fields
                    or not all(isinstance(value, str) and value.strip() for value in reference.values())):
                raise ValueError
        if dispatch['submission']['host'] != bindings['foreman']['host_id']:
            raise ValueError
        incident = packet['conciergeIncident']
        if not isinstance(incident, str) or not incident.strip() or incident == dispatch['identity']['incident_id']:
            raise ValueError
        # The sealed packet is sufficient for the bounded per-effect check.
        # Read mutable retained history only during explicit native qualification.
        if bridge is None:
            return evidence_digest(packet)
        outbox = read_private(Path(state_dir) / 'transport' / 'incident-outbox.json')
        if (not isinstance(outbox, dict) or outbox.get('version') != 1
                or not isinstance(outbox.get('incidents'), dict)):
            raise ValueError
        origin = outbox['incidents'][incident]
        if not isinstance(origin, dict) or origin.get('status') not in ('submitted', 'outcome_unknown'):
            raise ValueError
        _qualified_attempt(config, origin['identity'], origin['attempt'],
                           config['conciergeThreadId'], bindings['concierge']['host_id'])
        private_directory(Path(config['incidentReceiptStore']).parent, create=False)
        snapshot = read_private(config['incidentReceiptStore'])
        if (not isinstance(snapshot, dict) or snapshot.get('version') != 1
                or not isinstance(snapshot.get('incidents'), dict)
                or evidence_digest(snapshot['incidents'][incident]) != packet['conciergeRecordDigest']):
            raise ValueError
        receipt_record = snapshot['incidents'][incident]
        if (not isinstance(receipt_record, dict) or receipt_record.get('identity') != origin['identity']
                or not isinstance(receipt_record.get('attempts'), list)
                or [item for item in receipt_record['attempts']
                    if isinstance(item, dict) and item.get('id') == origin['attempt']['id']] != [origin['attempt']]):
            raise ValueError
        if bridge is not None:
            parent = bridge.call('read_thread', {'threadId': config['parentThreadId'], 'turnLimit': 1,
                                                'includeOutputs': False, 'maxOutputCharsPerItem': 1})
            if (not isinstance(parent, dict) or not isinstance(parent.get('thread'), dict)
                    or parent['thread'].get('id') != config['parentThreadId']
                    or parent['thread'].get('hostId') != packet['parentHost']):
                raise ValueError
            if (not match_submission(bridge, dispatch['identity'], dispatch['attempt'], dispatch['submission'])
                    or not verify_ack(bridge, {'thread': config['foremanThreadId'],
                                              'host': bindings['foreman']['host_id']},
                                      dispatch['receipt'], dispatch['identity']['incident_id'],
                                      dispatch['attempt']['id'], 'receipt')
                    or reconcile_snapshot(bridge, origin, snapshot, bindings['repairOwner'])
                    != 'owned_continuation_matched'):
                raise ValueError
            after = read_private(config['incidentReceiptStore'])
            if (not isinstance(after, dict) or not isinstance(after.get('incidents'), dict)
                    or evidence_digest(after['incidents'][incident]) != packet['conciergeRecordDigest']):
                raise ValueError
        return evidence_digest(packet)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ValueError('incident_routing_not_ready') from None



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


class ResetOperationBusy(BlockingIOError):
    """Another operation owns the reset lock."""


@contextmanager
def reset_operation_lock(state_dir, *, blocking=True, create=True):
    """Serialize Daybreak episode transitions with a confirmed reset attempt."""
    root = private_directory(state_dir, create=False)
    fd = os.open(root / 'reset.lock', (os.O_CREAT if create else 0) | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('private_owned_reset_lock_required')
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as error:
            raise ResetOperationBusy() from error
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
        if config.get('incidentRoutingEnabled') is True:
            if value.get('routingReadiness') != routing_readiness(config, state_dir):
                return {'mode': 'maintenance', 'reason': 'incident_routing_not_ready'}
        return {'mode': 'active', 'reference': value['reference']}
    except (OSError, ValueError):
        return {'mode': 'maintenance', 'reason': 'activation_record_unreadable'}


def set_mode(config, state_dir, mode, reference, *, live_preflight=None, qualification_digest=None):
    if mode not in ('active', 'maintenance') or not isinstance(reference, str) or not reference.strip():
        raise ValueError('mode_and_verified_operator_reference_required')
    path = Path(state_dir) / 'activation.json'
    # Always persist maintenance first. A failed activation preflight must not
    # leave a former activation armed for the next boot.
    decision = {'mode': 'maintenance', 'reference': reference, 'at': time.time(), 'decisionId': str(uuid.uuid4())}
    with decision_lock(state_dir):
        save(path, decision)
    if mode == 'active':
        readiness = None
        if config.get('incidentRoutingEnabled') is True:
            if live_preflight is not None:
                raise ValueError('routing_requires_real_preflight')
            from controller import validate_config
            validate_config(config, activate=True)
            readiness = routing_readiness(config, state_dir)
            if qualification_digest != readiness:
                raise ValueError('reviewed_qualification_digest_required')
        elif qualification_digest is not None:
            raise ValueError('unexpected_qualification_digest')
        if live_preflight is None:
            from preflight import preflight
            live_preflight = preflight
        report = live_preflight(config, Path(state_dir), check_live=True)
        with decision_lock(state_dir):
            if read_private(path) != decision:
                raise RuntimeError('activation_superseded_by_newer_decision')
            if readiness is not None:
                if (not isinstance(report, dict) or report.get('routingReadiness') != readiness
                        or routing_readiness(config, state_dir) != readiness):
                    raise ValueError('incident_routing_not_ready')
                if config.get('_configPath'):
                    current = read_private(config['_configPath'])
                    if not isinstance(current, dict) or configuration_binding(current) != configuration_binding(config):
                        raise ValueError('activation_configuration_changed')
            active = {'mode': 'active', 'reference': reference, 'at': time.time(),
                      'decisionId': decision['decisionId'], 'binding': configuration_binding(config)}
            if readiness is not None:
                active['routingReadiness'] = readiness
            save(path, active)
    return delivery_status(config, state_dir)


if __name__ == '__main__':
    from configuration import load_config, runtime_arguments
    parser = argparse.ArgumentParser(description=__doc__)
    runtime_arguments(parser)
    parser.add_argument('--mode', choices=('maintenance', 'active'), required=True)
    parser.add_argument('--reference', required=True,
                        help='Verified operator decision; a supplied string is not itself approval')
    parser.add_argument('--qualification-sha256', help='Digest of the reviewed routing qualification')
    args = parser.parse_args()
    print(json.dumps(set_mode(load_config(args.config, args.state_dir), args.state_dir,
                              args.mode, args.reference, qualification_digest=args.qualification_sha256), indent=2))
