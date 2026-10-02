#!/usr/bin/env python3
"""Read local safeguard health without waking a model or contacting accounts."""
import argparse
import json
from pathlib import Path
import time

from storage import read_private


def status(config_path, state_dir, now=None):
    config = read_private(config_path)
    root = Path(state_dir)
    if root.resolve() != Path(config['stateDir']).resolve():
        raise ValueError('configured_state_directory_mismatch')
    health = read_private(root / 'health.json', {})
    episodes = {}
    for name in ('main', 'daybreak'):
        episode = read_private(root / f'{name}.json', {}).get('episode')
        if episode:
            episodes[name] = {key: episode.get(key) for key in ('id', 'active', 'budgetStatus', 'waitingSpendUsd')}
            episodes[name]['deliveries'] = {event['kind']: event.get('deliveries', {}) for event in episode['events']}
    modified = (root / 'health.json').stat().st_mtime if (root / 'health.json').exists() else 0
    age = (time.time() if now is None else now) - modified
    return {'enabledInConfig': config.get('enabled') is True, 'observer': health,
            'observerFresh': bool(modified and 0 <= age < max(120, config['intervalSeconds'] * 3)),
            'transport': read_private(root / 'transport' / 'health.json', {'healthy': False, 'reason': 'not_started'}),
            'episodes': episodes}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True); parser.add_argument('--state-dir', required=True)
    args = parser.parse_args()
    print(json.dumps(status(args.config, args.state_dir), indent=2))
