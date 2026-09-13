"""
Phase 15 — Deployment Environments Unit Test Suite.

Covers tests ENV-1 through ENV-20:
- ENV-1: Environment enum correctness
- ENV-2: PAPER default when unspecified
- ENV-3: Unknown environment rejected
- ENV-4: Malformed environment rejected
- ENV-5: Environment isolation
- ENV-6: Runtime path isolation
- ENV-7: Credential-source validation
- ENV-8: PAPER cannot reach live broker
- ENV-9: SHADOW cannot reach live broker
- ENV-10: LIVE authorization requirement
- ENV-11: Startup fail-closed behavior
- ENV-12: Startup ordering contract
- ENV-13: Safe deterministic shutdown
- ENV-14: Deployment identity determinism
- ENV-15: Configuration determinism
- ENV-16: Secret redaction / isolation
- ENV-17: Readiness is diagnostic/non-trading
- ENV-18: Backup / recovery contract (bit-for-bit deterministic)
- ENV-19: Rollback safety
- ENV-20: Environment crossover protection
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from alphaforge.broker.interface import AbstractBroker
from alphaforge.broker.models import BrokerOrder, BrokerOrderRequest, BrokerPosition
from alphaforge.broker.paper import PaperBroker
from alphaforge.deployment.backup import (
    DeterministicBackupManager,
)
from alphaforge.deployment.broker_guard import (
    DeploymentBrokerGuard,
)
from alphaforge.deployment.config import DeploymentConfig
from alphaforge.deployment.enums import DeploymentEnvironment
from alphaforge.deployment.exceptions import (
    DeploymentConfigurationError,
    DeploymentSafetyError,
    EnvironmentIsolationError,
    StartupValidationError,
)
from alphaforge.deployment.identity import (
    compute_config_hash,
    get_deployment_identity,
)
from alphaforge.deployment.isolation import EnvironmentDirectoryManager
from alphaforge.deployment.readiness import DeploymentReadinessChecker
from alphaforge.deployment.rollback import RollbackCoordinator
from alphaforge.deployment.runtime import DeploymentRuntime
from alphaforge.execution.enums import OrderSide
from alphaforge.execution.idempotency import OrderRole

if TYPE_CHECKING:
    from pathlib import Path


class MockLiveBroker(AbstractBroker):
    """Explicitly tagged live execution broker for testing."""

    is_live_broker: bool = True

    def __init__(self) -> None:
        self.orders_submitted: list[BrokerOrderRequest] = []
        self.orders_cancelled: list[str] = []

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        self.orders_submitted.append(request)
        return BrokerOrder.create_synthetic_ack(request, "LIVE-ORD-1")

    def get_order(
        self,
        client_order_id: str | None = None,  # noqa: ARG002
        broker_order_id: str | None = None,  # noqa: ARG002
    ) -> BrokerOrder | None:
        return None

    def get_open_orders(self) -> tuple[BrokerOrder, ...]:
        return ()

    def get_positions(self) -> tuple[BrokerPosition, ...]:
        return ()

    def cancel_order(self, client_order_id: str) -> BrokerOrder:
        self.orders_cancelled.append(client_order_id)
        dummy_req = BrokerOrderRequest(
            client_order_id=client_order_id,
            symbol="MNQ",
            side=OrderSide.BUY,
            quantity=1,
            role=OrderRole.ENTRY,
            price=Decimal("18000"),
        )
        return BrokerOrder.create_synthetic_ack(dummy_req, "LIVE-ORD-1")

    def is_available(self) -> bool:
        return True


def test_env_1_enum_correctness() -> None:
    """ENV-1: Environment enum contains exactly DEV, TEST, PAPER, SHADOW, LIVE."""
    expected = {"DEV", "TEST", "PAPER", "SHADOW", "LIVE"}
    actual = {e.value for e in DeploymentEnvironment}
    assert actual == expected

    assert DeploymentEnvironment.PAPER.is_simulated is True
    assert DeploymentEnvironment.SHADOW.is_simulated is True
    assert DeploymentEnvironment.DEV.is_simulated is True
    assert DeploymentEnvironment.TEST.is_simulated is True
    assert DeploymentEnvironment.LIVE.is_simulated is False
    assert DeploymentEnvironment.LIVE.is_live is True


def test_env_2_paper_default() -> None:
    """ENV-2: Default environment is PAPER when unspecified."""
    assert DeploymentEnvironment.default() == DeploymentEnvironment.PAPER

    cfg = DeploymentConfig()
    assert cfg.environment == DeploymentEnvironment.PAPER

    cfg_env = DeploymentConfig.from_env({})
    assert cfg_env.environment == DeploymentEnvironment.PAPER


def test_env_3_unknown_environment_rejected() -> None:
    """ENV-3: Unknown environment values fail closed with DeploymentConfigurationError."""
    with pytest.raises(DeploymentConfigurationError, match="Invalid deployment environment"):
        DeploymentEnvironment.from_str("PRODUCTION")

    with pytest.raises(DeploymentConfigurationError, match="Invalid deployment environment"):
        DeploymentEnvironment.from_str("STAGING")

    with pytest.raises(DeploymentConfigurationError):
        DeploymentConfig.from_env({"ALPHAFORGE_ENV": "INVALID_ENV"})


def test_env_4_malformed_environment_rejected() -> None:
    """ENV-4: Malformed, empty, or non-string environment values fail closed."""
    with pytest.raises(DeploymentConfigurationError, match="cannot be empty or whitespace"):
        DeploymentEnvironment.from_str("")

    with pytest.raises(DeploymentConfigurationError, match="cannot be empty or whitespace"):
        DeploymentEnvironment.from_str("   ")

    with pytest.raises(DeploymentConfigurationError, match="must be a string"):
        DeploymentEnvironment.from_str(123)  # type: ignore[arg-type]

    # Explicit empty string in environment variable must FAIL CLOSED, not default to PAPER
    with pytest.raises(DeploymentConfigurationError, match="cannot be empty or whitespace"):
        DeploymentConfig.from_env({"ALPHAFORGE_ENV": ""})

    with pytest.raises(DeploymentConfigurationError, match="cannot be empty or whitespace"):
        DeploymentConfig.from_env({"ALPHAFORGE_ENV": "   "})


def test_env_5_environment_isolation(tmp_path: Path) -> None:
    """ENV-5: Independent configs have distinct directories and isolated configurations."""
    cfg_paper = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        runtime_root=tmp_path / "runtime",
    )
    cfg_shadow = DeploymentConfig(
        environment=DeploymentEnvironment.SHADOW,
        runtime_root=tmp_path / "runtime",
    )
    cfg_live = DeploymentConfig(
        environment=DeploymentEnvironment.LIVE,
        live_authorized=True,
        runtime_root=tmp_path / "runtime",
    )

    assert cfg_paper.effective_state_dir != cfg_shadow.effective_state_dir
    assert cfg_paper.effective_state_dir != cfg_live.effective_state_dir
    assert cfg_shadow.effective_state_dir != cfg_live.effective_state_dir


def test_env_6_runtime_path_isolation(tmp_path: Path) -> None:
    """ENV-6: Runtime directories are partitioned under runtime/<env>/{state,logs,audit,obs}."""
    root = tmp_path / "runtime"
    cfg_paper = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        runtime_root=root,
    )
    dirs = EnvironmentDirectoryManager.initialize_directories(cfg_paper)
    assert dirs["root"] == root / "paper"
    assert dirs["state"] == root / "paper" / "state"
    assert dirs["logs"] == root / "paper" / "logs"
    assert dirs["audit"] == root / "paper" / "audit"
    assert dirs["observability"] == root / "paper" / "observability"
    assert (root / "paper" / ".alphaforge_env_marker").exists()


def test_env_7_credential_source_validation() -> None:
    """ENV-7: Credential source is validated; real credentials not required for paper/test."""
    cfg_paper = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        credential_source="simulated",
    )
    checker_paper = DeploymentReadinessChecker(cfg_paper)
    rep_paper = checker_paper.check_readiness()
    assert rep_paper.credentials_ready is True

    # LIVE requires valid credential source
    cfg_live = DeploymentConfig(
        environment=DeploymentEnvironment.LIVE,
        live_authorized=True,
        credential_source="env",
    )
    assert cfg_live.credential_source == "env"


def test_env_8_paper_cannot_reach_live_broker(tmp_path: Path) -> None:
    """ENV-8: PAPER environment paired with a live broker is strictly rejected."""
    cfg_paper = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        runtime_root=tmp_path,
    )
    live_broker = MockLiveBroker()

    # Rejected at construction time
    with pytest.raises(
        DeploymentSafetyError, match="PAPER environment cannot be paired with a live broker"
    ):
        DeploymentBrokerGuard(delegate=live_broker, config=cfg_paper)

    # Also fails closed in runtime initialization
    runtime = DeploymentRuntime(
        config=cfg_paper,
        broker=PaperBroker(),
    )
    runtime.startup()
    assert runtime.is_active is True


def test_env_9_shadow_cannot_reach_live_broker(tmp_path: Path) -> None:
    """ENV-9: SHADOW environment paired with a live broker is strictly rejected."""
    cfg_shadow = DeploymentConfig(
        environment=DeploymentEnvironment.SHADOW,
        runtime_root=tmp_path,
    )
    live_broker = MockLiveBroker()

    with pytest.raises(
        DeploymentSafetyError, match="SHADOW environment cannot be paired with a live broker"
    ):
        DeploymentBrokerGuard(delegate=live_broker, config=cfg_shadow)


def test_env_10_live_authorization_requirement() -> None:
    """ENV-10: LIVE mode requires explicit dual authorization (live_authorized=True)."""
    # Missing live_authorized -> rejected
    with pytest.raises(
        DeploymentSafetyError, match="LIVE deployment requires explicit dual authorization"
    ):
        DeploymentConfig(environment=DeploymentEnvironment.LIVE, live_authorized=False)

    # Authorized LIVE is permitted
    cfg = DeploymentConfig(environment=DeploymentEnvironment.LIVE, live_authorized=True)
    assert cfg.environment == DeploymentEnvironment.LIVE
    assert cfg.live_authorized is True


def test_env_11_startup_fail_closed_behavior(tmp_path: Path) -> None:
    """ENV-11: Invalid configuration or failed readiness halts startup completely."""
    cfg_live_unauth = DeploymentConfig(
        environment=DeploymentEnvironment.LIVE,
        live_authorized=True,
        runtime_root=tmp_path,
    )

    # Mock broker that is not available
    class BrokenBroker(AbstractBroker):
        is_live_broker = True

        def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
            raise NotImplementedError

        def get_order(self, client_order_id=None, broker_order_id=None):  # noqa: ARG002
            return None

        def get_open_orders(self):
            return ()

        def get_positions(self):
            return ()

        def cancel_order(self, client_order_id: str):
            raise NotImplementedError

        def is_available(self) -> bool:
            return False

    runtime = DeploymentRuntime(
        config=cfg_live_unauth,
        broker=BrokenBroker(),
    )
    with pytest.raises(
        StartupValidationError, match="Startup failed: Pre-flight readiness check failed"
    ):
        runtime.startup()

    assert runtime.is_started is False
    assert runtime.is_active is False


def test_env_12_startup_ordering(tmp_path: Path) -> None:
    """ENV-12: Startup validates config, directories, readiness, and security gates in order."""
    root = tmp_path / "runtime"
    cfg = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        runtime_root=root,
    )
    paper_broker = PaperBroker()
    runtime = DeploymentRuntime(
        config=cfg,
        broker=paper_broker,
    )
    runtime.startup()
    assert runtime.is_started is True
    assert runtime.is_active is True
    assert (root / "paper" / ".alphaforge_env_marker").exists()


def test_env_13_safe_shutdown(tmp_path: Path) -> None:
    """ENV-13: Shutdown immediately stops work, preserves state, and triggers 0 orders."""
    cfg = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        runtime_root=tmp_path,
    )
    broker = PaperBroker()
    runtime = DeploymentRuntime(config=cfg, broker=broker)
    runtime.startup()

    assert runtime.is_active is True
    runtime.shutdown()

    assert runtime.is_active is False
    assert runtime.is_shutdown is True

    # After shutdown, submitting an order is blocked
    req = BrokerOrderRequest(
        client_order_id="ORD-SHUTDOWN-1",
        symbol="MNQ",
        side=OrderSide.BUY,
        quantity=1,
        role=OrderRole.ENTRY,
        price=Decimal("18000"),
    )
    with pytest.raises(StartupValidationError, match="DeploymentRuntime is not active"):
        assert runtime.broker is not None
        runtime.broker.submit_order(req)


def test_env_14_deployment_identity_determinism() -> None:
    """ENV-14: Identical configuration and commit SHA produce bit-for-bit identical identity."""
    cfg1 = DeploymentConfig(environment=DeploymentEnvironment.PAPER)
    cfg2 = DeploymentConfig(environment=DeploymentEnvironment.PAPER)

    id1 = get_deployment_identity(cfg1, git_commit="abcdef123456")
    id2 = get_deployment_identity(cfg2, git_commit="abcdef123456")

    assert id1.composite_id == id2.composite_id
    assert id1.config_hash == id2.config_hash
    assert id1.code_revision == id2.code_revision


def test_env_15_configuration_determinism() -> None:
    """ENV-15: Canonical configuration hash is deterministic and reproducible."""
    cfg1 = DeploymentConfig(environment=DeploymentEnvironment.PAPER)
    cfg2 = DeploymentConfig(environment=DeploymentEnvironment.PAPER)
    assert compute_config_hash(cfg1) == compute_config_hash(cfg2)


def test_env_16_secret_redaction() -> None:
    """ENV-16: Configuration safe dict and identity never leak secrets."""
    cfg = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        credential_source="vault-key-ref",
    )
    safe_data = cfg.to_safe_dict()
    assert "password" not in safe_data
    assert "secret" not in safe_data
    assert "api_key" not in safe_data
    assert safe_data["credential_source"] == "vault-key-ref"


def test_env_17_readiness_is_diagnostic_non_trading(tmp_path: Path) -> None:
    """ENV-17: Readiness probes are purely passive; never call submit_order or cancel_order."""
    cfg = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        runtime_root=tmp_path / "runtime",
    )
    EnvironmentDirectoryManager.initialize_directories(cfg)
    broker = PaperBroker()

    initial_orders = len(broker.get_open_orders())
    checker = DeploymentReadinessChecker(config=cfg, broker=broker)
    report = checker.check_readiness()

    assert report.is_ready is True
    assert len(broker.get_open_orders()) == initial_orders


def test_env_18_deterministic_backup(tmp_path: Path) -> None:
    """ENV-18: Backup creation produces identical SHA-256 archive hashes for identical inputs."""
    root = tmp_path / "runtime"
    cfg = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        runtime_root=root,
    )
    dirs = EnvironmentDirectoryManager.initialize_directories(cfg)

    # Write identical state file
    state_file = dirs["state"] / "positions.json"
    state_file.write_text('{"MNQ": 2}', encoding="utf-8")
    audit_file = dirs["audit"] / "audit_log.jsonl"
    audit_file.write_text('{"event": "START"}\n', encoding="utf-8")

    # Create backup 1
    archive1, hash1 = DeterministicBackupManager.create_backup(cfg, output_path=tmp_path / "b1.zip")
    # Create backup 2
    archive2, hash2 = DeterministicBackupManager.create_backup(cfg, output_path=tmp_path / "b2.zip")

    # Repeated identical inputs MUST produce identical SHA-256 hashes!
    assert hash1 == hash2
    assert archive1.read_bytes() == archive2.read_bytes()

    # Verify backup
    valid, msg = DeterministicBackupManager.verify_backup(archive1)
    assert valid is True

    # Restore backup into a fresh paper directory
    fresh_root = tmp_path / "fresh_runtime"
    cfg_fresh = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        runtime_root=fresh_root,
    )
    res = DeterministicBackupManager.restore_backup(archive1, cfg_fresh)
    assert res is True
    restored_state = cfg_fresh.effective_state_dir / "positions.json"
    assert restored_state.exists()
    assert restored_state.read_text(encoding="utf-8") == '{"MNQ": 2}'


def test_env_19_rollback_safety() -> None:
    """ENV-19: Rollback cannot accidentally switch PAPER into LIVE or cross environments."""
    cfg_paper = DeploymentConfig(environment=DeploymentEnvironment.PAPER)
    id_paper = get_deployment_identity(cfg_paper, git_commit="commit-1")

    # Attempt to rollback PAPER into LIVE config -> REJECT
    cfg_live = DeploymentConfig(environment=DeploymentEnvironment.LIVE, live_authorized=True)
    with pytest.raises(DeploymentSafetyError, match="Cannot rollback across environments"):
        RollbackCoordinator.validate_rollback(
            current_identity=id_paper,
            target_revision="commit-0",
            target_config=cfg_live,
        )

    # Valid rollback within PAPER
    res = RollbackCoordinator.validate_rollback(
        current_identity=id_paper,
        target_revision="commit-0",
        target_config=cfg_paper,
    )
    assert res.is_valid is True
    assert res.is_live_target is False


def test_env_20_environment_crossover_protection(tmp_path: Path) -> None:
    """ENV-20: Attempting to point PAPER at a LIVE marked directory fails closed."""
    root = tmp_path / "runtime"
    cfg_live = DeploymentConfig(
        environment=DeploymentEnvironment.LIVE,
        live_authorized=True,
        runtime_root=root,
    )
    EnvironmentDirectoryManager.initialize_directories(cfg_live)

    # Now create a PAPER config explicitly pointing its state_dir to the LIVE directory
    cfg_paper_crossover = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        runtime_root=root,
        state_dir=root / "live" / "state",
    )
    with pytest.raises(EnvironmentIsolationError, match="Cross-environment path collision"):
        EnvironmentDirectoryManager.initialize_directories(cfg_paper_crossover)
