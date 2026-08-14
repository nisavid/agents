from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from ._activation_support import canonical_value
from ._control_maintenance_activation_support import (
    ControlMaintenanceSmokeObservation,
    PreparedControlMaintenanceActivation,
    assert_control_maintenance_rollback_smoke_observation,
    assert_control_maintenance_smoke_observation,
)
from ._routine_activation_support import receipt_digest_inventory
from ._routine_staged_client_support import (
    InstalledClientSmokeObserver,
    installed_client_smoke_process,
)


@contextmanager
def installed_control_maintenance_smoke_process(
    deployment: object,
    prepared: PreparedControlMaintenanceActivation,
    support_root: Path,
) -> Iterator[InstalledClientSmokeObserver]:
    """Validate control state before each real installed-client smoke call."""

    root = prepared.initial.canonical_root
    with installed_client_smoke_process(
        deployment,
        root,
        support_root,
    ) as observer:
        real_smoke = deployment._run_activation_smoke

        def observe_control_state(
            smoke_root: Path,
            activation_lock_fd: int,
        ):
            if smoke_root != root:
                raise AssertionError("control maintenance smoke root disagrees")
            journal = canonical_value((root / "transaction.json").read_bytes())
            observation = ControlMaintenanceSmokeObservation(
                journal=journal,
                active_raw=(root / "active.json").read_bytes(),
                deployment_raw=(root / "deployment.json").read_bytes(),
                receipt_digests=receipt_digest_inventory(root),
            )
            if journal["phase"] == "candidate-smoke":
                assert_control_maintenance_smoke_observation(
                    observation,
                    prepared,
                )
            elif journal["phase"] == "rollback-smoke":
                assert_control_maintenance_rollback_smoke_observation(
                    observation,
                    prepared,
                )
            else:
                raise AssertionError(
                    "control maintenance installed-client smoke phase disagrees"
                )
            return real_smoke(smoke_root, activation_lock_fd)

        with mock.patch.object(
            deployment,
            "_run_activation_smoke",
            side_effect=observe_control_state,
        ):
            yield observer
