# Codex usage safeguard

Monitor the default Codex account and one pinned Daybreak account with a local
observer. At exact quota exhaustion, request a reset confirmation or ask the
verified Codex Foreman to coordinate the authorized pause. The observer never
redeems resets, buys credits, or resumes work.

The companion renews an app connection through normal MCP startup. It publishes
the inherited route; the observer validates the existing registration and exact
destinations independently before sending. A failed chat destination does not
block a reachable Foreman. Outages produce bounded retries and hourly local
reminders without recurring model turns. Delivery remains in persistent
maintenance until explicitly activated after live preflight.

- [Policy and acceptance contract](SPEC.md)
- [Operating procedure, installation, and recovery](OPERATING.md)
- [Capture a candidate for an explicitly appointed local caller and approval inbox](OPERATING.md#capture-and-adopt-a-local-caller)
- [Public configuration shape](config.example.json), with synthetic identities

`safeguard.py` owns quota policy; `controller.py` owns the singleton and durable
delivery receipts; `transport.py` owns connection renewal. `native_bridge.py`
isolates the installed app's private MCP adapter. Normal `companion.py` startup
exposes no tools; its opt-in capture mode returns a read-only caller candidate.
`caller_capture.py` checks invocation metadata and exact local native readback.
`activation.py` records persistent maintenance and explicit activation.
Credentials are read from the existing profiles without refresh or changes.

The supported target is Linux, Python 3.11+, Node 22+, systemd user services, and
the ChatGPT app's installed native MCP server. The private native and WHAM APIs
can change; an app update requires the read-only release preflight. Normal MCP
startup supplies a route but no new owner identity. Automatic startup when no
Codex session loads, archived-owner recovery, and arbitrary future app versions
have not been established. Capture is fixture-tested; complete live invocation
metadata, caller reuse, self-delivery, restart survival, and account-switch
persistence require separate operational qualification.

## Validate

Run from this package:

```sh
npm ci --ignore-scripts --omit=dev
python3 -m unittest discover -p 'test_*.py'
npm test
```

The transport tests bind disposable Unix sockets; a sandbox may require an
approved fixture-only run outside its socket restrictions. Tests never read real
credentials or contact a live app. `preflight.py --check-live` is a separate
explicit, read-only operational check.

The repository checks also apply:

```sh
python3 -m unittest tests.test_validate_provingkit_retirement
python3 scripts/validate_provingkit_retirement.py .
git diff --check
```
