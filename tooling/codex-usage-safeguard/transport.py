"""Own app-route adoption, bounded retries, and one local notice per outage.

The companion only offers inherited routes. Only the singleton observer opens
native connections, using its existing genuine registration and exact targets.
Quota, approval, and delivery state are never modified by this module.
"""
import fcntl
from contextlib import nullcontext
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import time
import uuid

from native_bridge import NativeBridge, NativeBridgeError, verify_destination
from incident_delivery import handoff_fault, reconcile_snapshot
from storage import private_directory, read_private, save


def binding(config):
    fields = {key: config[key] for key in ('nativeContext', 'foremanThreadId', 'parentThreadId',
                                          'nodeBinary', 'nativeBridgeScript')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def owner_binding(config):
    # Destinations are checked independently at adoption; changing the parent
    # does not invalidate an inherited route for the unchanged caller/Foreman.
    fields = {key: config[key] for key in ('nativeContext', 'nodeBinary', 'nativeBridgeScript')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def socket_identity(pipe):
    if not isinstance(pipe, str) or not pipe.startswith('/') or '\n' in pipe or '\r' in pipe:
        raise ValueError('invalid_inherited_route')
    info = Path(pipe).lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('owned_native_socket_required')
    return {'device': info.st_dev, 'inode': info.st_ino, 'ctimeNs': info.st_ctime_ns}


def publish_candidate(state_dir, config, environment, *, now=None):
    pipe = environment.get('CODEX_APP_TOOLS_PIPE_PATH')
    if not pipe:
        return {'status': 'no_inherited_route'}
    route = {'pipe': pipe, 'socket': socket_identity(pipe), 'binding': binding(config),
             'ownerBinding': owner_binding(config)}
    key = hashlib.sha256(json.dumps(route, sort_keys=True).encode()).hexdigest()
    root = private_directory(private_directory(state_dir) / 'transport')
    fd = os.open(root / 'publish.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            candidates = read_private(root / 'candidates.json', {})
        except json.JSONDecodeError:
            candidates = {}
        # Reconstruct only malformed routing metadata. Ownership/permission
        # failures from read_private must still fail closed, never be repaired here.
        if not isinstance(candidates, dict) or not all(valid_route(r) for r in candidates.values()):
            candidates = {}
        previous = candidates.get(key)
        # Hundreds of MCP copies publish the same generation without rewriting
        # or making a stale route appear newer than a later app generation.
        if previous is None:
            published_at = time.time() if now is None else now
            if candidates:
                latest = max(item['publishedAt'] for item in candidates.values())
                if published_at <= latest:
                    # This field orders publications even if wall time moves back.
                    published_at = math.nextafter(latest, math.inf)
            if not math.isfinite(published_at):
                raise ValueError('route_publication_order_exhausted')
            candidates[key] = {**route, 'generation': key, 'publishedAt': published_at}
            candidates = dict(sorted(candidates.items(), key=lambda item: item[1]['publishedAt'])[-16:])
            save(root / 'candidates.json', candidates)
    return {'status': 'candidate_published', 'generation': key}


def desktop_notice(message):
    subprocess.run(['notify-send', '--app-name=Codex quota safeguard', '--urgency=critical',
                    'Quota safeguard needs attention', message], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)


def valid_route(route):
    try:
        timestamp = route.get('publishedAt') if isinstance(route, dict) else None
        finite_timestamp = type(timestamp) in (int, float) and math.isfinite(timestamp)
    except OverflowError:
        finite_timestamp = False
    return (isinstance(route, dict)
            and all(isinstance(route.get(k), str) and route[k] for k in ('pipe', 'binding', 'generation'))
            and isinstance(route.get('socket'), dict)
            and finite_timestamp)


def failure_diagnostic(error):
    """Only allowlisted metadata crosses into the persisted health record."""
    if isinstance(error, NativeBridgeError):
        return error.diagnostic()
    if isinstance(error, FileNotFoundError):
        return {'kind': 'native_socket_missing', 'stage': 'route_validation'}
    if isinstance(error, PermissionError):
        return {'kind': 'native_socket_access_denied', 'stage': 'route_validation'}
    if isinstance(error, ValueError) and str(error) in (
            'registration_binding_changed', 'socket_generation_changed', 'owned_native_socket_required'):
        return {'kind': str(error), 'stage': 'route_validation'}
    return {'kind': 'native_route_check_failed', 'stage': 'route_validation'}


class _Destination:
    def __init__(self, state_dir, config, recipient, *, factory=None, clock=time.time):
        self.registry = private_directory(private_directory(state_dir) / 'transport')
        self.root = private_directory(self.registry / recipient)
        self.recipient = recipient
        self.config = config; self.clock = clock
        self.factory = factory or (lambda cfg, route: NativeBridge(cfg, route['pipe']))
        self.bridge = None; self.route = None; self.checked_at = 0
        self.attempted = {}

    def _health(self):
        try:
            value = read_private(self.root / 'health.json', {})
            if not isinstance(value, dict): raise ValueError('invalid_health_record')
            for key in ('retryAt', 'observedAt', 'lastFailureAt'):
                if key in value and (type(value[key]) not in (int, float) or not math.isfinite(value[key])):
                    raise ValueError('invalid_health_record')
            if 'attempts' in value and (type(value['attempts']) is not int or value['attempts'] < 0):
                raise ValueError('invalid_health_record')
            if 'candidateSet' in value and (not isinstance(value['candidateSet'], list)
                    or not all(isinstance(item, str) for item in value['candidateSet'])):
                raise ValueError('invalid_health_record')
            return value
        except (OSError, ValueError):
            # Health is reconstructible; never apply this recovery to delivery,
            # approval or quota ledgers. Persist a new incident if still offline.
            return {}

    def _verify(self, route):
        if (route.get('ownerBinding') != owner_binding(self.config) and
                not ('ownerBinding' not in route and route['binding'] == binding(self.config))):
            raise ValueError('registration_binding_changed')
        if socket_identity(route['pipe']) != route['socket']:
            raise ValueError('socket_generation_changed')
        candidate = self.factory(self.config, route)
        try:
            verify_destination(candidate, self.config[self.recipient + 'ThreadId'], self.recipient,
                               role_binding=self.config.get('roleBindings', {}).get(self.recipient))
        except Exception:
            candidate.close()
            raise
        return candidate

    def _healthy(self, route):
        save(self.root / 'health.json', {'healthy': True, 'observedAt': self.clock(),
             'generation': route['generation'], 'recipient': self.recipient})

    def _failed(self, candidates, failures):
        value = self._health()
        value.update(healthy=False, observedAt=self.clock(), recipient=self.recipient,
                     reason='native_route_unavailable_or_registration_rejected', candidateSet=sorted(candidates))
        if failures:
            value.update(lastFailures=failures[-3:], lastFailureAt=self.clock())
        value['attempts'] = value.get('attempts', 0) + 1
        value['retryAt'] = self.clock() + min(300, 5 * 2 ** min(value['attempts'] - 1, 6))
        save(self.root / 'health.json', value)

    def connection(self):
        now = self.clock()
        failures = []
        if self.bridge is not None:
            if 0 <= now - self.checked_at < 60:
                return self.bridge
            try:
                if socket_identity(self.route['pipe']) != self.route['socket']:
                    raise ValueError('socket_generation_changed')
                verify_destination(self.bridge, self.config[self.recipient + 'ThreadId'], self.recipient,
                                   role_binding=self.config.get('roleBindings', {}).get(self.recipient))
                self.checked_at = now; self._healthy(self.route)
                return self.bridge
            except Exception as error:
                failures.append(failure_diagnostic(error))
                self.close()
        try:
            candidates = read_private(self.registry / 'candidates.json', {})
            if not isinstance(candidates, dict) or not all(valid_route(r) for r in candidates.values()):
                raise ValueError('invalid_registry')
        except (OSError, ValueError):
            candidates = {}  # Still try the independently persisted accepted route.
        health = self._health()
        if (health.get('observedAt', now) <= now < health.get('retryAt', 0)
                and sorted(candidates) == health.get('candidateSet')):
            raise RuntimeError('transport_waiting_for_local_retry')
        try:
            accepted = read_private(self.root / 'accepted.json')
            if accepted is None:
                accepted = read_private(self.registry / 'accepted.json')  # Read-only legacy fallback.
            if accepted is not None and not valid_route(accepted): raise ValueError('invalid_accepted_route')
        except (OSError, ValueError):
            accepted = None
        routes = sorted(candidates.values(), key=lambda route: route['publishedAt'], reverse=True)
        if accepted:
            routes = [accepted] + [r for r in routes if r['generation'] != accepted['generation']]
        # Two candidate checks per poll bound IPC work. Failed routes cool down;
        # an app producing several candidates cannot starve quota observation.
        routes = [r for r in routes if not 0 <= now - self.attempted.get(r['generation'], -1e9) < 300]
        for route in routes[:2]:
            self.attempted[route['generation']] = now
            try:
                bridge = self._verify(route)
            except Exception as error:
                failures.append(failure_diagnostic(error))
                continue
            try:
                save(self.root / 'accepted.json', route)
                self._healthy(route)
            except Exception:
                bridge.close()
                raise
            self.bridge = bridge; self.route = route; self.checked_at = now
            self.attempted.pop(route['generation'], None)
            return bridge
        self._failed(candidates, failures)
        raise RuntimeError('transport_unavailable_requires_registration_or_app_recovery')

    def close(self):
        if self.bridge is not None: self.bridge.close()
        self.bridge = None; self.route = None; self.checked_at = 0


class Transport:
    """Independent destination connections and one bounded local outage notice."""
    reminder_seconds = 3600

    def __init__(self, state_dir, config, *, factory=None, notifier=desktop_notice, clock=time.time,
                 delivery_guard=None, delivery_lock=None, emergency_throttle=None):
        self.root = private_directory(private_directory(state_dir) / 'transport')
        self.clock = clock; self.notifier = notifier; self.config = config
        self.delivery_guard = delivery_guard; self.delivery_lock = delivery_lock
        roles = ('foreman', 'parent', 'concierge') if config.get('incidentRoutingEnabled') is True else ('foreman', 'parent')
        self.destinations = {role: _Destination(state_dir, config, role, factory=factory, clock=clock)
                             for role in roles}
        self.health = {}
        self.intake_status = 'not_needed'
        self.incident_error = False
        self.incident_registration_failed = False
        self.receipt_checked_at = None
        self.receipt_cursor = 0
        self.emergency_throttle = emergency_throttle if emergency_throttle is not None else {}

    def emergency_notice(self, *, intake_failed, active, used_percent):
        """Issue a bounded alert for failed intake and urgent or unknown context."""
        if intake_failed is not True:
            return False
        unknown_activity = type(active) is not bool
        unknown_quota = (type(used_percent) not in (int, float) or not math.isfinite(used_percent)
                         or not 0 <= used_percent <= 100)
        if not (unknown_activity or unknown_quota or (active and used_percent >= 95)):
            return False
        path = self.root / 'emergency-notice.json'
        now = self.clock()
        last_local = self.emergency_throttle.get('attemptedAt')
        if last_local is not None and 0 <= now - last_local < self.reminder_seconds:
            return False
        message = ('Quota safeguard agent intake failed while quota is urgent or its context is unknown. '
                   'Inspect the safeguard and reconcile any uncertain delivery.')
        def fallback():
            self.emergency_throttle['attemptedAt'] = now
            self.notifier(message)
            return True
        try:
            old = read_private(path, {})
            if not isinstance(old, dict):
                raise ValueError('invalid_emergency_notice_state')
            if 'attemptedAt' in old and (type(old['attemptedAt']) not in (int, float)
                                         or not math.isfinite(old['attemptedAt'])):
                raise ValueError('invalid_emergency_notice_state')
        except (OSError, ValueError):
            return fallback()
        if 'attemptedAt' in old:
            last = old['attemptedAt']
            if 0 <= now - last < self.reminder_seconds:
                return False
        record = {'status': 'attempting', 'attemptedAt': now}
        try:
            save(path, record)
        except (OSError, ValueError):
            return fallback()
        self.emergency_throttle['attemptedAt'] = now
        try:
            self.notifier(message)
        except Exception:
            record['status'] = 'failed'
            save(path, record)
            raise
        record['status'] = 'submitted'
        save(path, record)
        return True

    def _handoff(self, incident_id, context):
        target = self.config['conciergeThreadId']
        attempt_id = incident_id + ':1'
        identity = {'incident_id': incident_id, 'sender': self.config['nativeContext']['threadId'],
                    'sender_host': self.config['nativeContext']['host'], 'recipient': target,
                    'recipient_host': self.config['roleBindings']['concierge']['host_id']}
        receipt_ack = 'QUOTA-ACK/1 ' + json.dumps({'attempt': attempt_id, 'incident': incident_id,
                                                   'kind': 'receipt'}, sort_keys=True, separators=(',', ':'))
        payload = (f'Quota safeguard incident {incident_id}; attempt {attempt_id}. '
                   f'Inspect {self.root / "health.json"} and {self.root / "incident-outbox.json"}; '
                   f'Use {Path(__file__).with_name("incident_receipts.py")} and '
                   f'{self.config["incidentReceiptStore"]} only after the reviewed store handover; '
                   'verify native evidence independently, assign the existing repair owner, '
                   'and record receipt and an owned continuation. Submission is not receipt. '
                   'Do not reset, switch accounts, or release holds. '
                   f'For receipt, emit this exact assistant message, with no other text:\n{receipt_ack}')
        attempt = {'id': attempt_id, 'caller': identity['sender'], 'caller_host': identity['sender_host'],
                   'target': target, 'target_host': identity['recipient_host'], 'payload': payload}
        try:
            with self.delivery_lock() if self.delivery_lock is not None else nullcontext():
                path = self.root / 'incident-outbox.json'
                state = read_private(path, {'version': 1, 'incidents': {}})
                if not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('incidents'), dict):
                    raise ValueError('invalid_incident_outbox')
                retained = state['incidents'].get(incident_id)
                if retained is not None:
                    if (not isinstance(retained, dict) or retained.get('identity') != identity or
                            not isinstance(retained.get('attempt'), dict) or retained['attempt'].get('id') != attempt_id):
                        raise ValueError('incident_identity_conflict')
                    attempt = retained['attempt']
                permitted = self.delivery_guard is None or self.delivery_guard()
                bridge = self.destinations['concierge'].bridge if permitted else None
                handoff_fault(path, identity, attempt, bridge, context=context)
        except Exception:
            self.incident_error = True
            self.intake_status = 'outcome_unknown'
            raise
        return self._outbox_status()

    def _outbox_status(self):
        state = read_private(self.root / 'incident-outbox.json', {'version': 1, 'incidents': {}})
        if not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('incidents'), dict):
            raise ValueError('invalid_incident_outbox')
        values = list(state['incidents'].values())
        statuses = [value.get('status') for value in values if isinstance(value, dict)]
        if len(statuses) != len(values) or any(
                status not in ('not_attempted', 'outcome_unknown', 'submitted') for status in statuses):
            raise ValueError('invalid_incident_outbox')
        for value in values:
            history = value.get('reconciliation', [])
            if not isinstance(history, list) or not all(isinstance(item, dict) for item in history):
                raise ValueError('invalid_incident_outbox')
        statuses = [value['status'] for value in values if not (
            isinstance(value.get('reconciliation'), list) and value['reconciliation'] and
            value['reconciliation'][-1].get('stage') in
            ('receipt_matched', 'owner_accepted_matched', 'owned_continuation_matched'))]
        if 'not_attempted' in statuses:
            return 'waiting_for_route'
        if 'outcome_unknown' in statuses:
            return 'outcome_unknown'
        return 'submitted' if statuses else 'not_needed'

    def reconcile_pending(self):
        """Bound native reads to one incident per minute, after the quota sample."""
        bridge = self.destinations['concierge'].bridge
        now = self.clock()
        if bridge is None or (self.receipt_checked_at is not None and now - self.receipt_checked_at < 60):
            return
        path = self.root / 'incident-outbox.json'
        state = read_private(path, {'version': 1, 'incidents': {}})
        if not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('incidents'), dict):
            raise ValueError('invalid_incident_outbox')
        receipt_path = self.config['incidentReceiptStore']
        before = read_private(receipt_path)
        digest = (hashlib.sha256(json.dumps(before, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                  if before is not None else None)
        route = self.destinations['concierge'].route
        generation = route.get('generation') if isinstance(route, dict) else None
        candidates = []
        for key, value in state['incidents'].items():
            if not isinstance(value, dict) or value.get('status') not in ('submitted', 'outcome_unknown'):
                continue
            history = value.get('reconciliation', [])
            if not isinstance(history, list) or not all(isinstance(item, dict) for item in history):
                raise ValueError('invalid_incident_outbox')
            if (not history or history[-1].get('stage') != 'owned_continuation_matched'
                    or history[-1].get('receiptDigest') != digest or history[-1].get('routeGeneration') != generation):
                candidates.append(key)
        candidates.sort()
        if not candidates:
            return
        incident = candidates[self.receipt_cursor % len(candidates)]
        self.receipt_cursor += 1
        self.receipt_checked_at = now
        if before is None:
            stage = 'pending'
        else:
            stage = reconcile_snapshot(bridge, state['incidents'][incident], before,
                                       self.config['roleBindings']['repairOwner'])
            after = read_private(receipt_path)
            if after is None or hashlib.sha256(json.dumps(after, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != digest:
                stage = 'pending'
        current = state['incidents'][incident]
        history = current.setdefault('reconciliation', [])
        if not isinstance(history, list) or not all(isinstance(item, dict) for item in history):
            raise ValueError('invalid_incident_outbox')
        if (not history or history[-1].get('stage') != stage or history[-1].get('receiptDigest') != digest
                or history[-1].get('routeGeneration') != generation):
            receipt_record = (before.get('incidents', {}).get(incident)
                              if isinstance(before, dict) and isinstance(before.get('incidents'), dict) else None)
            history.append({'stage': stage, 'observedAt': now, 'receiptDigest': digest,
                            'receiptRecord': receipt_record, 'routeGeneration': generation})
            save(path, state)
        self.intake_status = self._outbox_status()

    def observation_failed(self, account):
        if account not in ('main', 'daybreak'):
            raise ValueError('unknown_account')
        path = self.root / 'observation-faults.json'
        records = read_private(path, {})
        if not isinstance(records, dict):
            raise ValueError('invalid_observation_faults')
        current = records.get(account)
        if not isinstance(current, dict) or current.get('active') is not True:
            current = {'incident': str(uuid.uuid4()), 'active': True}
            records[account] = current
            save(path, records)
        self.intake_status = self._handoff(current['incident'], {'reason': 'quota_observation_unavailable',
                                                               'account': account})
        return current['incident']

    def observation_recovered(self, account):
        path = self.root / 'observation-faults.json'
        records = read_private(path, {})
        if not isinstance(records, dict):
            raise ValueError('invalid_observation_faults')
        current = records.get(account)
        if isinstance(current, dict) and current.get('active') is True:
            self.intake_status = self._handoff(current['incident'], {'reason': 'quota_observation_unavailable',
                                                                   'account': account})
            current['active'] = False
            save(path, records)

    def _retry_known_unsent(self):
        path = self.root / 'incident-outbox.json'
        state = read_private(path, {'version': 1, 'incidents': {}})
        if not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('incidents'), dict):
            raise ValueError('invalid_incident_outbox')
        bridge = self.destinations['concierge'].bridge
        for value in list(state['incidents'].values()):
            if not isinstance(value, dict) or value.get('status') != 'not_attempted':
                continue
            identity = value['identity']
            if (identity['sender'] != self.config['nativeContext']['threadId'] or
                    identity['sender_host'] != self.config['nativeContext']['host'] or
                    identity['recipient'] != self.config['conciergeThreadId'] or
                    identity['recipient_host'] != self.config['roleBindings']['concierge']['host_id']):
                continue
            with self.delivery_lock() if self.delivery_lock is not None else nullcontext():
                permitted = self.delivery_guard is None or self.delivery_guard()
                handoff_fault(path, identity, value['attempt'], bridge if permitted else None)
        self.intake_status = self._outbox_status()

    def _record_health(self, failures):
        try:
            old = read_private(self.root / 'health.json', {})
            if not isinstance(old, dict): old = {}
        except (OSError, ValueError):
            old = {}
        recipients = {role: destination._health() for role, destination in self.destinations.items()}
        for role, diagnostic in failures.items():
            recipients[role] = {**recipients[role], 'healthy': False}
            if diagnostic is not None: recipients[role]['lastFailures'] = [diagnostic]
        healthy = all(value.get('healthy') is True for value in recipients.values())
        now = self.clock()
        if healthy:
            value = {'healthy': True, 'observedAt': now, 'recipients': recipients, 'incident': None,
                     'lastRecoveredIncident': old.get('incident') or old.get('lastRecoveredIncident')}
        else:
            value = dict(old)
            if (not isinstance(value.get('incident'), str) or not value['incident']
                    or type(value.get('startedAt')) not in (int, float) or not math.isfinite(value['startedAt'])):
                value = {'incident': str(uuid.uuid4()), 'startedAt': now}
            value.update(healthy=False, observedAt=now, recipients=recipients,
                         reason='one_or_more_destinations_unavailable')
            notice = value.get('notice', {})
            if not isinstance(notice, dict): notice = {}
            for key in ('attemptedAt', 'attempts'):
                if key in notice and (type(notice[key]) not in (int, float) or not math.isfinite(notice[key])):
                    notice = {}; break
            # Old records had no attempt timestamp: use the incident start.
            last = notice.get('attemptedAt', value['startedAt'])
            due = not notice or not 0 <= now - last < self.reminder_seconds
            if due and self.config.get('incidentRoutingEnabled') is not True:
                count = notice.get('attempts', 1 if notice else 0) + 1
                value['notice'] = {'status': 'attempting', 'attemptedAt': now, 'attempts': count}
                # Persist intent before the side effect, including retry attempts.
                save(self.root / 'health.json', value)
                unavailable = ', '.join(role for role, item in recipients.items() if not item.get('healthy'))
                try:
                    self.notifier('Quota delivery is unavailable for: ' + unavailable + '. '
                        'Check the safeguard status and app connection diagnostics. Quota reads continue. '
                        'A socket access denial does not establish invalid registration. '
                        'Use only supported recovery with the existing genuine caller and approved destinations. '
                        'No reset or resume was performed. This local reminder repeats at most hourly until recovery.')
                    value['notice']['status'] = 'submitted'
                except Exception:
                    value['notice']['status'] = 'failed'
        if not healthy:
            try:
                save(self.root / 'health.json', value)
            except (OSError, ValueError):
                if self.config.get('incidentRoutingEnabled') is True:
                    self.incident_registration_failed = True
                raise
        if self.config.get('incidentRoutingEnabled') is True:
            incident_id = value.get('incident') or old.get('incident')
            if incident_id:
                try:
                    self.intake_status = self._handoff(incident_id, {'reason': value.get('reason', old.get('reason')),
                                                                     'recipients': value.get('recipients', {})})
                except Exception:
                    self.health = {**(old if healthy else value), 'healthy': False,
                                   'intakeError': 'incident_handoff_unavailable'}
                    return
        save(self.root / 'health.json', value)
        self.health = value

    def connection(self):
        available = False; failures = {}; self.incident_error = False
        self.incident_registration_failed = False
        for role, destination in self.destinations.items():
            try:
                destination.connection()
                available = True
            except Exception as error:
                destination.close()
                # One malformed/unreadable recipient record cannot discard a
                # different recipient's successfully verified connection.
                failures[role] = None if isinstance(error, RuntimeError) else failure_diagnostic(error)
        self._record_health(failures)
        if self.config.get('incidentRoutingEnabled') is True and not self.incident_error:
            try:
                self._retry_known_unsent()
            except Exception:
                self.incident_error = True
                self.intake_status = 'outcome_unknown'
                self.health = {**self.health, 'healthy': False, 'intakeError': 'incident_outbox_unavailable'}
        if not available:
            raise RuntimeError('transport_unavailable_requires_registration_or_app_recovery')
        return self

    def for_recipient(self, recipient):
        """None means known unsent; the controller must not record send intent."""
        return self.destinations[recipient].bridge

    def close(self):
        for destination in self.destinations.values(): destination.close()
