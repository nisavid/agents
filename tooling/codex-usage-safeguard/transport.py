"""Own app-route adoption, bounded retries, and one local notice per outage.

The companion only offers inherited routes. Only the singleton observer opens
native connections, using its existing genuine registration and exact targets.
Quota, approval, and delivery state are never modified by this module.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time
import uuid

from native_bridge import NativeBridge, verify_bridge
from storage import private_directory, read_private, save


def binding(config):
    fields = {key: config[key] for key in ('nativeContext', 'foremanThreadId', 'parentThreadId',
                                          'nodeBinary', 'nativeBridgeScript')}
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
    route = {'pipe': pipe, 'socket': socket_identity(pipe), 'binding': binding(config)}
    key = hashlib.sha256(json.dumps(route, sort_keys=True).encode()).hexdigest()
    root = private_directory(private_directory(state_dir) / 'transport')
    fd = os.open(root / 'publish.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        candidates = read_private(root / 'candidates.json', {})
        previous = candidates.get(key)
        # Hundreds of MCP copies publish the same generation without rewriting
        # or making a stale route appear newer than a later app generation.
        if previous is None:
            candidates[key] = {**route, 'generation': key, 'publishedAt': time.time() if now is None else now}
            candidates = dict(sorted(candidates.items(), key=lambda item: item[1]['publishedAt'])[-16:])
            save(root / 'candidates.json', candidates)
    return {'status': 'candidate_published', 'generation': key}


def desktop_notice(message):
    subprocess.run(['notify-send', '--app-name=Codex quota safeguard', '--urgency=critical',
                    'Quota safeguard needs attention', message], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)


class Transport:
    def __init__(self, state_dir, config, *, factory=None, notifier=desktop_notice, clock=time.time):
        self.root = private_directory(private_directory(state_dir) / 'transport')
        self.config = config; self.clock = clock; self.notifier = notifier
        self.factory = factory or (lambda cfg, route: NativeBridge(cfg, route['pipe']))
        self.bridge = None; self.route = None; self.checked_at = 0
        self.attempted = {}

    def _verify(self, route):
        if route['binding'] != binding(self.config):
            raise ValueError('registration_binding_changed')
        if socket_identity(route['pipe']) != route['socket']:
            raise ValueError('socket_generation_changed')
        candidate = self.factory(self.config, route)
        try:
            verify_bridge(candidate, self.config['foremanThreadId'], self.config['parentThreadId'])
        except Exception:
            candidate.close()
            raise
        return candidate

    def _healthy(self, route):
        old = read_private(self.root / 'health.json', {})
        value = {'healthy': True, 'observedAt': self.clock(), 'generation': route['generation'],
                 'incident': None, 'lastRecoveredIncident': old.get('incident') or old.get('lastRecoveredIncident')}
        save(self.root / 'health.json', value)

    def _failed(self, candidates):
        value = read_private(self.root / 'health.json', {})
        first = not value.get('incident')
        if first:
            value = {'incident': str(uuid.uuid4()), 'startedAt': self.clock(), 'attempts': 0,
                     'notice': {'status': 'attempting'}}
        value.update(healthy=False, observedAt=self.clock(), reason='native_route_unavailable_or_registration_rejected',
                     candidateSet=sorted(candidates))
        value['attempts'] += 1
        value['retryAt'] = self.clock() + min(300, 5 * 2 ** min(value['attempts'] - 1, 6))
        # Notice intent precedes delivery. A crash at this point is uncertain,
        # never permission to repeat a desktop alert at every observer restart.
        save(self.root / 'health.json', value)
        if first:
            try:
                self.notifier('Open the ChatGPT app and check the quota safeguard status. '
                    'If recovery fails, re-register its existing owner through the supported '
                    'app workflow, verify Codex Foreman and parent, then replace the private registration. '
                    'Quota reads continue; pause delivery is unavailable. No reset or resume was performed.')
                value['notice']['status'] = 'submitted'
            except Exception:
                value['notice']['status'] = 'failed'
            save(self.root / 'health.json', value)

    def connection(self):
        now = self.clock()
        if self.bridge is not None:
            if now - self.checked_at < 60:
                return self.bridge
            try:
                if socket_identity(self.route['pipe']) != self.route['socket']:
                    raise ValueError('socket_generation_changed')
                verify_bridge(self.bridge, self.config['foremanThreadId'], self.config['parentThreadId'])
                self.checked_at = now; self._healthy(self.route)
                return self.bridge
            except Exception:
                self.close()
        candidates = read_private(self.root / 'candidates.json', {})
        health = read_private(self.root / 'health.json', {})
        if (now < health.get('retryAt', 0) and sorted(candidates) == health.get('candidateSet')):
            raise RuntimeError('transport_waiting_for_local_retry')
        accepted = read_private(self.root / 'accepted.json')
        routes = sorted(candidates.values(), key=lambda route: route['publishedAt'], reverse=True)
        if accepted:
            routes = [accepted] + [r for r in routes if r['generation'] != accepted['generation']]
        # Two candidate checks per poll bound IPC work. Failed routes cool down;
        # an app producing several candidates cannot starve quota observation.
        routes = [r for r in routes if now - self.attempted.get(r['generation'], -1e9) >= 300]
        for route in routes[:2]:
            self.attempted[route['generation']] = now
            try:
                bridge = self._verify(route)
            except Exception:
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
        self._failed(candidates)
        raise RuntimeError('transport_unavailable_requires_registration_or_app_recovery')

    def close(self):
        if self.bridge is not None: self.bridge.close()
        self.bridge = None; self.route = None; self.checked_at = 0
