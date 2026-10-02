"""Connection lifecycle tests use private fixture sockets and fake destinations."""
import copy
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from transport import Transport, publish_candidate


CONFIG = {'nativeContext': {'threadId': 'fixture-owner', 'turnId': 'fixture-turn', 'host': 'fixture'},
          'foremanThreadId': 'fixture-foreman', 'parentThreadId': 'fixture-parent',
          'nodeBinary': 'node', 'nativeBridgeScript': '/fixture/server.mjs'}


class FixtureBridge:
    def __init__(self, route, registry, calls):
        self.route = route; self.registry = registry; self.calls = calls; self.closed = False

    def call(self, tool, arguments):
        self.calls.append((self.route, tool, arguments['threadId']))
        mode = self.registry[self.route]
        if mode == 'deny':
            raise RuntimeError('fixture_authorization_rejected')
        target = arguments['threadId']
        return {'thread': {'id': 'wrong' if mode == 'wrong' else target,
                           'title': 'Codex Foreman' if target == 'fixture-foreman' else 'Parent'}}

    def send(self, target, prompt):
        self.calls.append((self.route, 'send', target))

    def close(self):
        self.closed = True


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.registry = {}; self.calls = []; self.alerts = []; self.sockets = []
        self.now = 1000

    def tearDown(self):
        for sock in self.sockets: sock.close()
        self.temp.cleanup()

    def route(self, name, mode='valid'):
        path = str(self.root / name)
        sock = socket.socket(socket.AF_UNIX); sock.bind(path)
        self.sockets.append(sock); self.registry[path] = mode
        return path

    def manager(self, config=None, notifier=None):
        return Transport(self.root, config or CONFIG,
            factory=lambda cfg, route: FixtureBridge(route['pipe'], self.registry, self.calls),
            notifier=notifier or self.alerts.append, clock=lambda: self.now)

    def publish(self, route, config=None):
        return publish_candidate(self.root, config or CONFIG, {'CODEX_APP_TOOLS_PIPE_PATH': route}, now=self.now)

    def test_concurrent_companions_coalesce_and_missing_pipe_keeps_candidate(self):
        pipe = self.route('app.sock')
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.publish(pipe), range(32)))
        self.assertEqual(publish_candidate(self.root, CONFIG, {}, now=self.now)['status'], 'no_inherited_route')
        candidates = json.loads((self.root / 'transport' / 'candidates.json').read_text())
        self.assertEqual(len(candidates), 1)
        self.assertEqual((self.root / 'transport' / 'candidates.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.calls, [])

    def test_adopts_only_after_both_exact_destinations_then_renews_after_failure(self):
        first = self.route('one.sock'); second = self.route('two.sock')
        self.publish(first); transport = self.manager()
        old = transport.connection()
        self.assertEqual([c[2] for c in self.calls], ['fixture-foreman', 'fixture-parent'])
        self.now += 1; self.publish(second)
        self.assertIs(transport.connection(), old)  # A healthy accepted route remains stable.
        self.registry[first] = 'deny'; self.now += 61
        new = transport.connection()
        self.assertEqual(new.route, second); self.assertTrue(old.closed)
        self.assertEqual(self.alerts, [])
        transport.close()
        self.assertEqual(self.manager().connection().route, second)

    def test_rejected_registration_latches_one_actionable_alert_until_recovery(self):
        pipe = self.route('rejected.sock', 'deny'); self.publish(pipe)
        transport = self.manager()
        for _ in range(4):
            with self.assertRaises(RuntimeError): transport.connection()
            self.now += 301
        self.assertEqual(len(self.alerts), 1)
        self.assertIn('registration', self.alerts[0])
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(len(self.alerts), 1)
        self.registry[pipe] = 'valid'; self.now += 301
        transport.connection()
        health = json.loads((self.root / 'transport' / 'health.json').read_text())
        self.assertTrue(health['healthy']); self.assertIsNone(health['incident'])
        self.registry[pipe] = 'deny'; self.now += 301
        with self.assertRaises(RuntimeError): transport.connection()
        self.assertEqual(len(self.alerts), 2)

    def test_changed_context_and_wrong_foreman_cannot_be_adopted(self):
        pipe = self.route('app.sock')
        changed = copy.deepcopy(CONFIG); changed['nativeContext']['turnId'] = 'unregistered'
        self.publish(pipe, changed)
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(self.calls, [])
        self.publish(pipe); self.registry[pipe] = 'wrong'; self.now += 301
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertFalse((self.root / 'transport' / 'accepted.json').exists())

    def test_failed_notification_is_recorded_without_repetition_after_restart(self):
        def broken(message): raise RuntimeError('desktop_unavailable')
        with self.assertRaises(RuntimeError): self.manager(notifier=broken).connection()
        health = json.loads((self.root / 'transport' / 'health.json').read_text())
        self.assertEqual(health['notice']['status'], 'failed')
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(self.alerts, [])

    def test_policy_and_delivery_files_survive_transport_upgrade_and_rollback(self):
        preserved = {'episode': {'id': 'open', 'active': True, 'resetIdempotencyKey': 'same',
            'events': [{'deliveries': {'foreman': {'status': 'outcome_unknown'}}}]},
            'identityGeneration': 'generation', 'approval': 'actual-human-reference'}
        state = self.root / 'main.json'; state.write_text(json.dumps(preserved))
        before = state.read_bytes(); self.publish(self.route('app.sock'))
        transport = self.manager(); transport.connection(); transport.close()
        self.manager().connection()
        self.assertEqual(state.read_bytes(), before)
        self.assertFalse(any(c[1] == 'send' for c in self.calls))

    def test_inherited_route_must_be_owned_socket_and_private_registry(self):
        regular = self.root / 'ordinary'; regular.write_text('not a socket')
        with self.assertRaises(ValueError): self.publish(str(regular))
        pipe = self.route('app.sock'); self.publish(pipe)
        os.chmod(self.root / 'transport' / 'candidates.json', 0o644)
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(len(self.alerts), 1)
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(len(self.alerts), 1)

    def test_corrupt_candidate_registry_cannot_displace_accepted_route(self):
        pipe = self.route('app.sock'); self.publish(pipe)
        manager = self.manager(); manager.connection(); manager.close()
        (self.root / 'transport' / 'candidates.json').write_text('invalid JSON')
        self.assertEqual(self.manager().connection().route, pipe)
        self.assertEqual(self.alerts, [])

    def test_unreadable_health_and_malformed_registry_record_one_recovery_incident(self):
        root = self.root / 'transport'; root.mkdir(mode=0o700)
        (root / 'health.json').write_text('bad JSON')
        (root / 'candidates.json').write_text('{"bad": {"publishedAt": "invalid"}}')
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(len(self.alerts), 1)
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(len(self.alerts), 1)


if __name__ == '__main__': unittest.main()
