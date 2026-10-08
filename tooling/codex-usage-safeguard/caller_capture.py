"""Read-only caller candidate from one constrained native tool invocation."""
import hashlib
import json
import os
import stat

from native_bridge import NativeBridge, NativeBridgeError
from transport import socket_identity


CAPTURE_TOOL = {
    'name': 'capture_native_context',
    'description': 'Return a read-only native caller candidate for the expected local control chat.',
    'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True},
}

IDENTITY_ALIASES = {
    'threadId': ('openai/threadId', 'openai/thread_id', 'codexThreadId', 'codex_thread_id',
                 'threadId', 'thread_id'),
    'turnId': ('openai/turnId', 'openai/turn_id', 'codexTurnId', 'codex_turn_id', 'turnId', 'turn_id'),
    'toolCallId': ('openai/toolCallId', 'openai/tool_call_id', 'codexCallId', 'codex_call_id',
                   'callId', 'call_id'),
}


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('malformed_runtime_metadata')
        value[key] = item
    return value


def runtime_identity(meta):
    if not isinstance(meta, dict):
        raise ValueError('complete_runtime_metadata_required')
    turn_metadata = meta.get('x-codex-turn-metadata', {})
    if isinstance(turn_metadata, str):
        try:
            turn_metadata = json.loads(turn_metadata, object_pairs_hook=unique_object)
        except (ValueError, RecursionError):
            raise ValueError('malformed_runtime_metadata') from None
    if not isinstance(turn_metadata, dict):
        raise ValueError('malformed_runtime_metadata')
    identity = {}; sources = {}
    for field, aliases in IDENTITY_ALIASES.items():
        values = {key: meta[key] for key in aliases if key in meta}
        nested_key = {'threadId': 'thread', 'turnId': 'turn', 'toolCallId': 'call'}[field]
        if nested_key in meta:
            nested = meta[nested_key]
            if not isinstance(nested, dict) or 'id' not in nested:
                raise ValueError('malformed_runtime_metadata')
            values[nested_key + '.id'] = nested['id']
        turn_key = {'threadId': 'thread_id', 'turnId': 'turn_id'}.get(field)
        if turn_key in turn_metadata:
            values['x-codex-turn-metadata.' + turn_key] = turn_metadata[turn_key]
        if not values:
            raise ValueError('complete_runtime_metadata_required')
        if not all(isinstance(value, str) and value and value.strip() == value
                   and not any(char.isspace() for char in value) for value in values.values()):
            raise ValueError('malformed_runtime_metadata')
        if len(set(values.values())) != 1:
            raise ValueError('conflicting_runtime_metadata')
        identity[field] = next(iter(values.values())); sources[field] = sorted(values)
    return identity, sources


def capture_settings(receipt_path, node_binary, native_bridge_script):
    settings = {'receipt': None, 'nodeBinary': node_binary, 'nativeBridgeScript': native_bridge_script}
    try:
        fd = os.open(receipt_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                return settings
            raw = stream.read(65537)
        if len(raw) > 65536:
            return settings
        settings['receipt'] = json.loads(raw, object_pairs_hook=unique_object)
        settings['receiptSha256'] = hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, UnicodeError, RecursionError):
        pass
    return settings


def capture_context(settings, params, environment):
    try:
        if not isinstance(params.get('arguments'), dict) or params['arguments']:
            raise ValueError('capture_arguments_forbidden')
        identity, sources = runtime_identity(params.get('_meta'))
        thread_id = identity['threadId']; turn_id = identity['turnId']; call_id = identity['toolCallId']
        receipt = settings['receipt']
        if (not isinstance(receipt, dict) or receipt.get('threadId') != thread_id
                or receipt.get('hostId') != 'local' or 'clientThreadId' in receipt):
            raise ValueError('expected_local_creation_receipt_required')
        explicit_host = 'CODEX_APP_TOOLS_CALLER_HOST_ID' in environment
        host = environment.get('CODEX_APP_TOOLS_CALLER_HOST_ID', 'local')
        if host != 'local':
            raise ValueError('explicit_local_runtime_host_required')
        pipe = environment.get('CODEX_APP_TOOLS_PIPE_PATH')
        if not pipe:
            raise ValueError('inherited_native_route_required')
        socket = socket_identity(pipe)
        context = {'threadId': thread_id, 'turnId': turn_id, 'host': host}
        bridge = NativeBridge({**settings, 'nativeContext': context}, pipe)
        try:
            readback = bridge.call('read_thread', {'threadId': thread_id, 'hostId': 'local',
                'turnLimit': 1, 'includeOutputs': False, 'maxOutputCharsPerItem': 1})
        finally:
            bridge.close()
        if socket_identity(pipe) != socket:
            raise ValueError('inherited_route_generation_changed')
        thread = readback.get('thread')
        if (not isinstance(thread, dict) or thread.get('id') != thread_id
                or thread.get('kind') != 'codex' or thread.get('hostId') != 'local'):
            raise ValueError('exact_local_native_readback_required')
        candidate = {'schemaVersion': 1, 'nativeContext': context, 'evidence': {
            'runtimeToolCallId': call_id, 'hostProvenance': ('explicit_runtime_host' if explicit_host
                else 'constrained_local_protocol_default'),
            'runtimeMetadataSources': sources,
            'expectedReceiptSha256': settings['receiptSha256'],
            'nativeReadback': {'threadId': thread_id, 'kind': 'codex', 'hostId': 'local'},
            'inheritedRouteSocket': socket}}
        candidate_json = json.dumps(candidate, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
        report = {'status': 'captured_candidate', 'candidateJson': candidate_json,
                  'sha256': hashlib.sha256(candidate_json.encode('utf-8')).hexdigest()}
    except (ValueError, OSError, NativeBridgeError, RecursionError) as error:
        report = {'status': 'unsupported_capture', 'reason': 'capture_evidence_unavailable'}
        # Error categories contain no raw native response or private payload.
        if isinstance(error, ValueError) and str(error) in (
                'capture_arguments_forbidden', 'complete_runtime_metadata_required',
                'malformed_runtime_metadata', 'conflicting_runtime_metadata',
                'expected_local_creation_receipt_required', 'explicit_local_runtime_host_required',
                'inherited_native_route_required', 'exact_local_native_readback_required',
                'inherited_route_generation_changed'):
            report['reason'] = str(error)
    return {'content': [{'type': 'text', 'text': json.dumps(report, sort_keys=True)}],
            'isError': report['status'] != 'captured_candidate'}
