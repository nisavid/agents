"""Private, locally supplied stop observations; never transport acknowledgments."""
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

from storage import private_directory, read_private, save


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def fields(value, names):
    require(isinstance(value, dict) and set(value) == set(names.split()), 'invalid_completion_fields')


def text(value):
    require(isinstance(value, str) and bool(value.strip()), 'nonempty_completion_text_required')


def references(value):
    require(isinstance(value, list) and bool(value), 'completion_evidence_required')
    for item in value:
        text(item)


def validate(record):
    fields(record, 'schemaVersion episodeId eventId accountId identityGeneration foremanThreadId observedAt inventory roots')
    require(type(record['schemaVersion']) is int and record['schemaVersion'] == 1, 'unsupported_completion_schema')
    for key in ('episodeId', 'eventId', 'accountId', 'foremanThreadId'):
        text(record[key])
    if record['identityGeneration'] is not None:
        text(record['identityGeneration'])
    observed = record['observedAt']
    require(type(observed) in (int, float), 'invalid_completion_time')
    try:
        finite = math.isfinite(observed)
    except OverflowError:
        finite = False
    require(finite and observed >= 0, 'invalid_completion_time')
    inventory = record['inventory']
    fields(inventory, 'complete evidenceReferences')
    require(type(inventory['complete']) is bool, 'invalid_inventory_coverage')
    references(inventory['evidenceReferences'])
    require(isinstance(record['roots'], list), 'invalid_root_inventory')
    seen = set()
    statuses = []
    for root in record['roots']:
        fields(root, 'threadId status workersComplete workers continuationHeld failureKind evidenceReferences')
        text(root['threadId'])
        require(root['threadId'] not in seen, 'duplicate_root')
        seen.add(root['threadId'])
        require(root['status'] in ('stopped', 'blocked', 'unknown', 'running'), 'invalid_stop_status')
        require(type(root['workersComplete']) is bool and type(root['continuationHeld']) is bool, 'invalid_stop_coverage')
        require(root['failureKind'] in (None, 'instruction_refusal', 'tool_denial', 'unknown'), 'invalid_failure_kind')
        references(root['evidenceReferences'])
        require(isinstance(root['workers'], list), 'invalid_worker_inventory')
        workers = set()
        for worker in root['workers']:
            fields(worker, 'id status')
            text(worker['id'])
            require(worker['id'] not in workers, 'duplicate_worker')
            workers.add(worker['id'])
            require(worker['status'] in ('stopped', 'blocked', 'unknown', 'running'), 'invalid_worker_status')
        if root['status'] == 'stopped':
            require(root['workersComplete'] and root['continuationHeld'] and root['failureKind'] is None
                    and all(w['status'] == 'stopped' for w in root['workers']), 'incomplete_stop_evidence')
        if root['status'] == 'blocked':
            require(root['failureKind'] is not None, 'blocked_failure_kind_required')
        statuses.append(root['status'])
    if not inventory['complete']:
        return 'partial' if 'stopped' in statuses else 'unknown'
    if all(s == 'stopped' for s in statuses):
        return 'stopped'
    if len(set(statuses)) > 1:
        return 'partial'
    return 'blocked' if statuses == ['blocked'] or set(statuses) == {'blocked'} else 'unknown'


def bind(record, config, directory, name):
    require(name in ('main', 'daybreak'), 'invalid_completion_account')
    state = read_private(directory / f'{name}.json', {})
    require(not state.get('delegatedTo'), 'completion_account_delegated')
    episode = state.get('episode') or {}
    require(episode.get('active') is True, 'no_active_pause_episode')
    events = [e for e in episode.get('events', [])
              if e.get('kind') == 'foreman_pause_required' and not e.get('invalidated')]
    require(bool(events), 'no_current_pause_event')
    require(record['episodeId'] == episode.get('id') and record['eventId'] == events[-1].get('id'), 'completion_episode_or_event_mismatch')
    require(record['accountId'] == state.get('accountId') == episode.get('accountId'), 'completion_account_mismatch')
    require(record['foremanThreadId'] == config['foremanThreadId'], 'completion_foreman_mismatch')
    if name == 'main':
        selected = read_private(directory / 'main-selection.json', {})
        require(bool(record['identityGeneration']) and record['identityGeneration'] == state.get('identityGeneration') == selected.get('generation')
                and selected.get('accountId') == record['accountId'], 'completion_generation_mismatch')
    else:
        pinned = next(a for a in config['accounts'] if a['name'] == name)
        require(record['identityGeneration'] is None and record['accountId'] == pinned['accountId'], 'completion_pinned_identity_mismatch')


def digest(record):
    return hashlib.sha256((json.dumps(record, indent=2) + '\n').encode()).hexdigest()


def record_completion(record, config, directory, name):
    directory = private_directory(directory, create=False)
    # This lock is separate from both the singleton observer and release locks.
    lock = directory / 'pause-completion.lock'
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077, 'private_completion_lock_required')
        fcntl.flock(fd, fcntl.LOCK_EX)
        result = validate(record)
        bind(record, config, directory, name)
        latest = directory / f'pause-completion-{name}.json'
        pointer = read_private(latest)
        if pointer is not None:
            validate_pointer(pointer)
        archive = private_directory(directory / 'pause-completions')
        fingerprint = digest(record)
        path = archive / f'{fingerprint}.json'
        if pointer:
            previous = read_archive(archive, pointer['sha256'])
            if previous.get('episodeId') == record['episodeId'] and previous.get('eventId') == record['eventId']:
                require(record['observedAt'] >= previous['observedAt'], 'older_completion_observation')
        if path.exists():
            require(read_archive(archive, fingerprint) == record, 'completion_archive_mismatch')
        else:
            save(path, record)
        save(latest, {'schemaVersion': 1, 'sha256': fingerprint})
        return {'status': result, 'sha256': fingerprint, 'evidenceAuthority': 'local_writer_observation'}
    finally:
        os.close(fd)


def read_archive(directory, fingerprint):
    fd = os.open(directory / f'{fingerprint}.json', os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
                'private_owned_file_required')
        data = stream.read()
    require(hashlib.sha256(data).hexdigest() == fingerprint, 'completion_digest_mismatch')
    return json.loads(data)


def validate_pointer(pointer):
    fields(pointer, 'schemaVersion sha256')
    require(type(pointer['schemaVersion']) is int and pointer['schemaVersion'] == 1
            and isinstance(pointer['sha256'], str) and re.fullmatch('[0-9a-f]{64}', pointer['sha256']), 'invalid_completion_pointer')


def completion_status(config, directory, name):
    try:
        directory = private_directory(directory, create=False)
        pointer = read_private(directory / f'pause-completion-{name}.json')
        if pointer is None:
            return {'status': 'unconfirmed', 'reason': 'no_completion_record'}
        validate_pointer(pointer)
        archive = private_directory(directory / 'pause-completions', create=False)
        record = read_archive(archive, pointer['sha256'])
        result = validate(record)
        bind(record, config, directory, name)
        return {'status': result, 'sha256': pointer['sha256'], 'observedAt': record['observedAt'],
                'evidenceAuthority': 'local_writer_observation'}
    except (ValueError, OSError, KeyError, TypeError, StopIteration) as error:
        return {'status': 'unconfirmed', 'reason': str(error) if isinstance(error, ValueError) else 'completion_record_unavailable'}
