"""Explicit private installation configuration; no credential mutation."""
from pathlib import Path
from storage import read_private, private_directory


def runtime_arguments(parser):
    parser.add_argument('--config', required=True)
    parser.add_argument('--state-dir', required=True)


def load_config(path, state_dir):
    config = read_private(path)
    if not isinstance(config, dict):
        raise ValueError('private_configuration_required')
    state = Path(state_dir).resolve()
    if state != Path(config['stateDir']).resolve():
        raise ValueError('configured_state_directory_mismatch')
    if state == Path(__file__).parent.resolve() or Path(__file__).parent.resolve() in state.parents:
        raise ValueError('state_must_live_outside_release')
    private_directory(state)
    for key in ('nodeBinary', 'nativeBridgeScript', 'foremanThreadId', 'parentThreadId', 'sharedLock'):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError('missing_installation_setting')
    for key in ('threadId', 'turnId', 'host'):
        if not isinstance(config.get('nativeContext', {}).get(key), str) or not config['nativeContext'][key].strip():
            raise ValueError('existing_genuine_registration_required')
    if Path(config['sharedLock']).resolve() == state / 'observer.lock':
        raise ValueError('legacy_shared_lock_must_differ_from_observer_lock')
    config['_configPath'] = str(Path(path).resolve())
    return config
