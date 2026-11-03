"""Offline candidate for a private, restart-persistent incident receipt journal."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import stat

from storage import private_directory, read_private, save


class ReceiptJournal:
    def __init__(self, path, *, read_only=False):
        self.path = Path(path)
        self.read_only = read_only
        private_directory(self.path.parent, create=False)
        self._read_store()

    def _read_store(self):
        value = read_private(self.path)
        if value is None:
            raise FileNotFoundError(self.path)
        if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('incidents'), dict):
            raise ValueError('invalid_receipt_store')
        return value

    @classmethod
    def create(cls, path):
        path = Path(path)
        private_directory(path.parent, create=False)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        save(path, {'version': 1, 'incidents': {}})
        return cls(path)

    @classmethod
    def open(cls, path, *, read_only=False):
        return cls(path, read_only=read_only)

    def _change(self, callback):
        if self.read_only:
            raise PermissionError('read_only_receipt_store')
        lock = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock, 'r+') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('private_receipt_lock_required')
            fcntl.flock(stream, fcntl.LOCK_EX)
            data = self._read_store()
            changed = callback(data['incidents'])
            if changed:
                save(self.path, data)
            return changed

    def record_incident(self, identity):
        if set(identity) != {'incident_id', 'sender', 'sender_host', 'recipient', 'recipient_host'} or not all(
                isinstance(value, str) and value for value in identity.values()):
            raise ValueError('invalid_incident_identity')
        def insert(incidents):
            old = incidents.get(identity['incident_id'])
            if old is not None:
                if old['identity'] != identity:
                    raise ValueError('incident_identity_conflict')
                return False
            incidents[identity['incident_id']] = {'identity': identity, 'attempts': [], 'observations': []}
            return True
        return self._change(insert)

    def read(self, incident_id):
        record = self._read_store()['incidents'].get(incident_id)
        return None if record is None else {**record, 'pending': True}

    def record_attempt(self, incident_id, attempt):
        if set(attempt) != {'id', 'caller', 'caller_host', 'target', 'target_host', 'payload'} or not all(
                isinstance(value, str) and value for value in attempt.values()):
            raise ValueError('invalid_attempt')
        def insert(incidents):
            incident = incidents.get(incident_id)
            if incident is None:
                raise ValueError('unknown_incident')
            if (attempt['target'] != incident['identity']['recipient']
                    or attempt['target_host'] != incident['identity']['recipient_host']
                    or incident_id not in attempt['payload']
                    or attempt['id'] not in attempt['payload']):
                raise ValueError('attempt_correlation_mismatch')
            for other in incidents.values():
                for old in other['attempts']:
                    if old['id'] == attempt['id']:
                        if old != attempt or other is not incident:
                            raise ValueError('attempt_identity_conflict')
                        return False
            incident['attempts'].append(attempt)
            return True
        return self._change(insert)

    def append(self, incident_id, observation):
        fields = {'id', 'kind', 'attempt_id', 'evidence', 'actor', 'host', 'turn', 'message'}
        if observation.get('kind') == 'received':
            fields.update({'submission_turn', 'submission_message'})
        if observation.get('kind') == 'continuation':
            fields.add('next_action')
        if set(observation) != fields or not all(
                isinstance(value, str) and value for value in observation.values()):
            raise ValueError('invalid_observation')
        if observation['kind'] not in {'received', 'owner_accepted', 'continuation'}:
            raise ValueError('unsupported_observation')
        def insert(incidents):
            incident = incidents.get(incident_id)
            if incident is None or not any(a['id'] == observation['attempt_id'] for a in incident['attempts']):
                raise ValueError('unknown_attempt')
            if (observation['kind'] == 'received' and
                    (observation['actor'] != incident['identity']['recipient'] or
                     observation['host'] != incident['identity']['recipient_host'])):
                raise ValueError('receipt_recipient_mismatch')
            for other in incidents.values():
                for old in other['observations']:
                    if old['id'] == observation['id']:
                        if old != observation or other is not incident:
                            raise ValueError('observation_identity_conflict')
                        return False
            incident['observations'].append(observation)
            return True
        return self._change(insert)

    def close(self):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    for name in ('incident', 'attempt', 'observation'):
        command = commands.add_parser(name)
        command.add_argument('--input', required=True)
        if name != 'incident':
            command.add_argument('--incident', required=True)
    show = commands.add_parser('show')
    show.add_argument('--incident', required=True)
    args = parser.parse_args()
    if args.command == 'init':
        journal = ReceiptJournal.create(args.store)
        result = {'created': True}
    else:
        journal = ReceiptJournal.open(args.store, read_only=args.command == 'show')
        if args.command == 'show':
            result = journal.read(args.incident)
        else:
            data = read_private(args.input)
            if not isinstance(data, dict):
                raise ValueError('private_input_required')
            if args.command == 'incident':
                result = {'created': journal.record_incident(data)}
            elif args.command == 'attempt':
                result = {'created': journal.record_attempt(args.incident, data)}
            else:
                result = {'created': journal.append(args.incident, data)}
    journal.close()
    print(json.dumps(result))


if __name__ == '__main__':
    main()
