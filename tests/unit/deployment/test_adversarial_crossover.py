"""
Phase 15 — Adversarial Crossover & Failure Test Suite.

Covers 16 explicit adversarial attack / mistake scenarios:
1. Missing environment -> PAPER only / safe default.
2. Unknown environment -> reject.
3. Malformed environment -> reject.
4. PAPER with LIVE credentials -> no live order capability.
5. SHADOW with LIVE credentials -> no live order capability.
6. LIVE without authorization -> reject.
7. LIVE with malformed authorization -> reject.
8. DEV cannot accidentally use LIVE runtime directory.
9. PAPER cannot write into LIVE state directory.
10. State paths are deterministic and environment-specific.
11. Secrets are absent from logs.
12. Secrets are absent from audit/observability payloads.
13. Failed startup cannot reach broker order submission.
14. Shutdown does not trigger trading actions.
15. Deployment identity is reproducible.
16. Rollback cannot accidentally switch PAPER into LIVE.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from alphaforge.broker.interface import AbstractBroker
from alphaforge.broker.models import BrokerOrder, BrokerOrderRequest
from alphaforge.broker.paper import PaperBroker
from alphaforge.deployment.backup import (
    LIVE_RECOVERY_CONFIRMATION_PHRASE,
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
from alphaforge.deployment.identity import get_deployment_identity
from alphaforge.deployment.isolation import EnvironmentDirectoryManager
from alphaforge.deployment.readiness import DeploymentReadinessChecker
from alphaforge.deployment.rollback import RollbackCoordinator
from alphaforge.deployment.runtime import DeploymentRuntime
from alphaforge.execution.enums import OrderSide
from alphaforge.execution.idempotency import OrderRole
from alphaforge.reconciliation.gate import ReconciliationGate
from alphaforge.security.config import SecurityConfig, TradingModeConfig
from alphaforge.security.credentials import BrokerCredentials, CredentialStore, SecretValue
from alphaforge.security.enums import TradingMode
from alphaforge.security.exceptions import SecurityConfigurationError
from alphaforge.security.redaction import redact_text
from alphaforge.security.startup import SecurityStartupGate

if TYPE_CHECKING:
    from pathlib import Path


class FakeLiveBroker(AbstractBroker):
    """Simulates a live broker adapter that connects to real exchanges."""

    is_live_broker = True

    def __init__(self) -> None:
        self.submitted_orders: list[BrokerOrderRequest] = []

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        self.submitted_orders.append(request)
        return BrokerOrder.create_synthetic_ack(request, "LIVE-FILL-100")

    def get_order(self, client_order_id=None, broker_order_id=None):  # noqa: ARG002
        return None

    def get_open_orders(self):
        return ()

    def get_positions(self):
        return ()

    def cancel_order(self, client_order_id: str):
        raise NotImplementedError

    def is_available(self) -> bool:
        return True


def test_adv_1_missing_environment_defaults_to_paper() -> None:
    """Adv-1: When environment variable is completely unset, runtime defaults strictly to PAPER."""
    cfg = DeploymentConfig.from_env({})
    assert cfg.environment == DeploymentEnvironment.PAPER
    assert cfg.environment.is_live is False


def test_adv_2_unknown_environment_rejected() -> None:
    """Adv-2: Arbitrary environment strings fail closed."""
    for bad_env in ["PROD", "STAGE", "SANDBOX", "REAL", "LOCAL"]:
        with pytest.raises(DeploymentConfigurationError):
            DeploymentConfig.from_env({"ALPHAFORGE_ENV": bad_env})


def test_adv_3_malformed_environment_rejected() -> None:
    """Adv-3: Whitespace, empty, or numeric environment inputs fail closed."""
    with pytest.raises(DeploymentConfigurationError):
        DeploymentConfig.from_env({"ALPHAFORGE_ENV": ""})
    with pytest.raises(DeploymentConfigurationError):
        DeploymentConfig.from_env({"ALPHAFORGE_ENV": "   "})
    with pytest.raises(DeploymentConfigurationError):
        DeploymentEnvironment.from_str(None)  # type: ignore[arg-type]


def test_adv_4_paper_with_live_credentials_cannot_execute_live(tmp_path: Path) -> None:
    """Adv-4: Even if live credentials exist in CredentialStore, PAPER cannot route live."""
    creds = CredentialStore()
    creds.set_live_credentials(
        BrokerCredentials(
            broker_id="zerodha",
            api_key="authentic_production_key_123",
            api_secret="authentic_production_secret_456",  # noqa: S106
            account_id="ACC998877",
            is_live=True,
        )
    )
    assert creds.has_live_credentials is True

    # User starts in PAPER
    cfg_paper = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        runtime_root=tmp_path,
    )
    live_broker = FakeLiveBroker()

    # Broker guard prevents wiring paper to live broker
    with pytest.raises(
        DeploymentSafetyError, match="PAPER environment cannot be paired with a live broker"
    ):
        DeploymentBrokerGuard(delegate=live_broker, config=cfg_paper)


def test_adv_5_shadow_with_live_credentials_cannot_execute_live(tmp_path: Path) -> None:
    """Adv-5: Even if live credentials exist, SHADOW cannot route to live broker."""
    cfg_shadow = DeploymentConfig(
        environment=DeploymentEnvironment.SHADOW,
        runtime_root=tmp_path,
    )
    live_broker = FakeLiveBroker()

    with pytest.raises(
        DeploymentSafetyError, match="SHADOW environment cannot be paired with a live broker"
    ):
        DeploymentBrokerGuard(delegate=live_broker, config=cfg_shadow)


def test_adv_6_live_without_authorization_rejected() -> None:
    """Adv-6: Specifying LIVE without live_authorized=True is rejected at config time."""
    with pytest.raises(
        DeploymentSafetyError, match="LIVE deployment requires explicit dual authorization"
    ):
        DeploymentConfig(environment=DeploymentEnvironment.LIVE, live_authorized=False)


def test_adv_7_live_with_malformed_authorization_rejected() -> None:
    """Adv-7: Ambiguous boolean strings for live authorization fail closed."""
    for bad_bool in ["maybe", "2", "trueish", "enable", ""]:
        with pytest.raises(
            (DeploymentSafetyError, DeploymentConfigurationError, SecurityConfigurationError)
        ):
            DeploymentConfig.from_env(
                {
                    "ALPHAFORGE_ENV": "LIVE",
                    "LIVE_AUTHORIZED": bad_bool,
                }
            )


def test_adv_8_dev_cannot_accidentally_use_live_directory(tmp_path: Path) -> None:
    """Adv-8: DEV environment cannot point into LIVE runtime directory tree."""
    root = tmp_path / "runtime"
    # Live runtime tree initialized
    cfg_live = DeploymentConfig(
        environment=DeploymentEnvironment.LIVE,
        live_authorized=True,
        runtime_root=root,
    )
    EnvironmentDirectoryManager.initialize_directories(cfg_live)

    # DEV config trying to hijack live root
    cfg_dev_hijack = DeploymentConfig(
        environment=DeploymentEnvironment.DEV,
        runtime_root=root,
        state_dir=root / "live" / "state",
    )
    with pytest.raises(EnvironmentIsolationError, match="Cross-environment path collision"):
        EnvironmentDirectoryManager.initialize_directories(cfg_dev_hijack)


def test_adv_9_paper_cannot_write_into_live_state(tmp_path: Path) -> None:
    """Adv-9: PAPER environment cannot attach to or overwrite LIVE state files."""
    root = tmp_path / "runtime"
    cfg_live = DeploymentConfig(
        environment=DeploymentEnvironment.LIVE,
        live_authorized=True,
        runtime_root=root,
    )
    dirs_live = EnvironmentDirectoryManager.initialize_directories(cfg_live)
    live_pos = dirs_live["state"] / "positions.json"
    live_pos.write_text('{"LIVE_POSITION": 10}', encoding="utf-8")

    # PAPER config trying to use LIVE state directory
    cfg_paper = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        runtime_root=root,
        state_dir=dirs_live["state"],
    )
    with pytest.raises(EnvironmentIsolationError):
        EnvironmentDirectoryManager.initialize_directories(cfg_paper)

    # Verify live position file was unchanged
    assert live_pos.read_text(encoding="utf-8") == '{"LIVE_POSITION": 10}'


def test_adv_10_state_paths_deterministic_and_environment_specific() -> None:
    """Adv-10: State paths are strictly derived from environment enum value."""
    cfg_p = DeploymentConfig(environment=DeploymentEnvironment.PAPER)
    cfg_s = DeploymentConfig(environment=DeploymentEnvironment.SHADOW)
    cfg_l = DeploymentConfig(environment=DeploymentEnvironment.LIVE, live_authorized=True)

    assert "paper" in cfg_p.effective_state_dir.as_posix()
    assert "shadow" in cfg_s.effective_state_dir.as_posix()
    assert "live" in cfg_l.effective_state_dir.as_posix()


def test_adv_11_secrets_absent_from_logs() -> None:
    """Adv-11: Logging and redaction guarantees secrets are scrubbed."""
    raw_log = "Connected with api_key=secret-live-broker-key and api_secret=super-private-pass"
    redacted = redact_text(raw_log)
    assert "secret-live-broker-key" not in redacted
    assert "super-private-pass" not in redacted


def test_adv_12_secrets_absent_from_deployment_payloads() -> None:
    """Adv-12: DeploymentConfig.to_safe_dict() contains no credentials or secret keys."""
    cfg = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        credential_source="secure_env",
    )
    safe = cfg.to_safe_dict()
    for sensitive in ["api_key", "secret", "password", "token", "private"]:
        assert sensitive not in safe


def test_adv_13_failed_startup_cannot_reach_broker_order_submission(tmp_path: Path) -> None:
    """Adv-13: If startup validation fails, broker order submissions are unreachable."""
    cfg = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        runtime_root=tmp_path,
    )

    class FailingSimBroker(PaperBroker):
        def is_available(self) -> bool:
            return False  # Will cause readiness check to fail

    runtime = DeploymentRuntime(config=cfg, broker=FailingSimBroker())

    # Startup should fail
    with pytest.raises(StartupValidationError):
        runtime.startup()

    assert runtime.is_active is False

    # Attempting to submit order via runtime's guarded broker MUST fail
    req = BrokerOrderRequest(
        client_order_id="ORD-BLOCKED",
        symbol="MNQ",
        side=OrderSide.BUY,
        quantity=1,
        role=OrderRole.ENTRY,
        price=Decimal("18000"),
    )
    with pytest.raises(StartupValidationError, match="DeploymentRuntime is not active"):
        assert runtime.broker is not None
        runtime.broker.submit_order(req)


def test_adv_14_shutdown_does_not_trigger_trading_actions(tmp_path: Path) -> None:
    """Adv-14: Clean shutdown flushes resources and never issues buy/sell/cancel orders."""
    cfg = DeploymentConfig(environment=DeploymentEnvironment.PAPER, runtime_root=tmp_path)
    broker = PaperBroker()
    runtime = DeploymentRuntime(config=cfg, broker=broker)
    runtime.startup()

    open_orders_before = broker.get_open_orders()
    positions_before = broker.get_positions()

    runtime.shutdown()

    assert broker.get_open_orders() == open_orders_before
    assert broker.get_positions() == positions_before


def test_adv_15_deployment_identity_reproducible() -> None:
    """Adv-15: Repeated generation of DeploymentIdentity produces identical composite IDs."""
    cfg = DeploymentConfig(environment=DeploymentEnvironment.PAPER)
    id1 = get_deployment_identity(cfg, git_commit="deadbeef12345678")
    id2 = get_deployment_identity(cfg, git_commit="deadbeef12345678")

    assert id1.composite_id == id2.composite_id
    assert id1.config_hash == id2.config_hash


def test_adv_16_rollback_cannot_accidentally_switch_paper_to_live() -> None:
    """Adv-16: Rollback procedure fails closed if target environment switches to LIVE."""
    cfg_paper = DeploymentConfig(environment=DeploymentEnvironment.PAPER)
    id_paper = get_deployment_identity(cfg_paper, git_commit="commit-10")

    cfg_live_target = DeploymentConfig(
        environment=DeploymentEnvironment.LIVE,
        live_authorized=True,
    )
    with pytest.raises(DeploymentSafetyError, match="Cannot rollback across environments"):
        RollbackCoordinator.validate_rollback(
            current_identity=id_paper,
            target_revision="commit-9",
            target_config=cfg_live_target,
        )


def test_adv_17_live_restore_safety_strictly_blocks_crossover(tmp_path: Path) -> None:
    """Adv-17: Crossover into LIVE is prohibited by default and by force."""
    root = tmp_path / "runtime"
    cfg_paper = DeploymentConfig(
        environment=DeploymentEnvironment.PAPER,
        runtime_root=root,
    )
    dirs_paper = EnvironmentDirectoryManager.initialize_directories(cfg_paper)
    (dirs_paper["state"] / "dummy.json").write_text("{}", encoding="utf-8")

    # Create paper backup
    paper_archive, _ = DeterministicBackupManager.create_backup(cfg_paper)

    cfg_live = DeploymentConfig(
        environment=DeploymentEnvironment.LIVE,
        live_authorized=True,
        runtime_root=root,
    )

    # 1. Generic restore into LIVE is completely blocked
    with pytest.raises(DeploymentSafetyError, match="Generic restore into LIVE is disabled"):
        DeterministicBackupManager.restore_backup(paper_archive, cfg_live)

    # 2. Even controlled live recovery REJECTS paper backup
    with pytest.raises(
        DeploymentSafetyError, match="Attempted to restore 'PAPER' backup into LIVE"
    ):
        DeterministicBackupManager.restore_live_recovery(
            archive_path=paper_archive,
            target_config=cfg_live,
            confirmation_phrase=LIVE_RECOVERY_CONFIRMATION_PHRASE,
        )


def test_live_without_security_startup_gate_is_rejected(tmp_path: Path) -> None:
    """
    Blocker 1 Adversarial Test:
    LIVE environment startup strictly fails closed if SecurityStartupGate is missing (None).
    """
    cfg_live = DeploymentConfig(
        environment=DeploymentEnvironment.LIVE,
        live_authorized=True,
        credential_source="env",
        runtime_root=tmp_path,
    )
    live_broker = FakeLiveBroker()

    # 1. DeploymentReadinessChecker must reject LIVE when security_startup_gate is None
    checker = DeploymentReadinessChecker(
        config=cfg_live,
        broker=live_broker,
        security_startup_gate=None,
    )
    report = checker.check_readiness()
    assert report.is_ready is False
    assert report.security_ready is False
    assert report.checks["security_ready"] is False

    # 2. DeploymentRuntime startup must raise StartupValidationError
    runtime = DeploymentRuntime(
        config=cfg_live,
        broker=live_broker,
        security_startup_gate=None,
    )
    with pytest.raises(
        StartupValidationError,
        match="Pre-flight readiness check failed|requires an active, verified SecurityStartupGate",
    ):
        runtime.startup()

    assert runtime.is_started is False
    assert runtime.is_active is False


def test_live_with_unverified_security_startup_gate_is_rejected(tmp_path: Path) -> None:
    """
    Blocker 1 Adversarial Test:
    LIVE environment startup strictly fails closed if SecurityStartupGate is present but unverified.
    """
    cfg_live = DeploymentConfig(
        environment=DeploymentEnvironment.LIVE,
        live_authorized=True,
        credential_source="env",
        runtime_root=tmp_path,
    )
    live_broker = FakeLiveBroker()

    sec_cfg = SecurityConfig(
        trading_mode_config=TradingModeConfig(
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=True,
        )
    )
    gate = SecurityStartupGate(security_config=sec_cfg)
    assert gate.is_verified is False

    # 1. DeploymentReadinessChecker rejects unverified gate
    checker = DeploymentReadinessChecker(
        config=cfg_live,
        broker=live_broker,
        security_startup_gate=gate,
    )
    report = checker.check_readiness()
    assert report.is_ready is False
    assert report.security_ready is False

    # 2. DeploymentRuntime rejects unverified gate
    runtime = DeploymentRuntime(
        config=cfg_live,
        broker=live_broker,
        security_startup_gate=gate,
    )
    with pytest.raises(StartupValidationError):
        runtime.startup()

    assert runtime.is_started is False
    assert runtime.is_active is False


def test_live_with_verified_security_startup_gate_is_allowed(tmp_path: Path) -> None:
    """
    Blocker 1 Affirmative Safety Test:
    LIVE environment with valid broker, credentials, and verified SecurityStartupGate is allowed.
    """
    root = tmp_path / "runtime"
    cfg_live = DeploymentConfig(
        environment=DeploymentEnvironment.LIVE,
        live_authorized=True,
        credential_source="env",
        runtime_root=root,
    )
    live_broker = FakeLiveBroker()

    sec_cfg = SecurityConfig(
        trading_mode_config=TradingModeConfig(
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=True,
        )
    )
    store = CredentialStore()
    store.set_live_credentials(
        BrokerCredentials(
            broker_id="zerodha",
            api_key=SecretValue("ProductionKeyABC987"),
            api_secret=SecretValue("ProductionSecretXYZ987"),
            account_id="ACC_PROD_1",
            is_live=True,
        )
    )
    recon_gate = ReconciliationGate(initially_open=True)
    gate = SecurityStartupGate(
        security_config=sec_cfg,
        credential_store=store,
        reconciliation_gate=recon_gate,
    )
    gate.verify_startup()
    assert gate.is_verified is True

    EnvironmentDirectoryManager.initialize_directories(cfg_live)

    # 1. DeploymentReadinessChecker confirms ready
    checker = DeploymentReadinessChecker(
        config=cfg_live,
        broker=live_broker,
        security_startup_gate=gate,
    )
    report = checker.check_readiness()
    assert report.is_ready is True
    assert report.security_ready is True
    assert report.broker_ready is True
    assert report.credentials_ready is True

    # 2. DeploymentRuntime starts up successfully
    runtime = DeploymentRuntime(
        config=cfg_live,
        broker=live_broker,
        security_startup_gate=gate,
    )
    runtime.startup()
    assert runtime.is_started is True
    assert runtime.is_active is True
    runtime.shutdown()
    assert runtime.is_active is False


def test_live_with_paper_broker_is_rejected(tmp_path: Path) -> None:
    """
    Blocker 2 Adversarial Test:
    LIVE environment paired with PaperBroker must raise DeploymentSafetyError at construction
    and order submission time, and fail readiness diagnostics.
    """
    cfg_live = DeploymentConfig(
        environment=DeploymentEnvironment.LIVE,
        live_authorized=True,
        credential_source="env",
        runtime_root=tmp_path,
    )
    paper_broker = PaperBroker()

    # 1. Construction-time rejection in DeploymentBrokerGuard
    with pytest.raises(
        DeploymentSafetyError,
        match="LIVE environment cannot be paired with a non-live broker",
    ):
        DeploymentBrokerGuard(delegate=paper_broker, config=cfg_live)

    # 2. Construction-time rejection in DeploymentRuntime
    with pytest.raises(
        DeploymentSafetyError,
        match="LIVE environment cannot be paired with a non-live broker",
    ):
        DeploymentRuntime(config=cfg_live, broker=paper_broker)

    # 3. Pre-flight readiness check failure in DeploymentReadinessChecker
    checker = DeploymentReadinessChecker(config=cfg_live, broker=paper_broker)
    report = checker.check_readiness()
    assert report.broker_ready is False
    assert report.is_ready is False


def test_live_with_simulated_broker_is_rejected(tmp_path: Path) -> None:
    """
    Blocker 2 Adversarial Test:
    LIVE environment paired with MockBroker or SimulatedBroker must raise DeploymentSafetyError.
    """

    class SimulatedBroker(AbstractBroker):
        def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
            return BrokerOrder.create_synthetic_ack(request, "SIM-1")

        def get_order(self, client_order_id=None, broker_order_id=None):  # noqa: ARG002
            return None

        def get_open_orders(self):
            return ()

        def get_positions(self):
            return ()

        def cancel_order(self, client_order_id: str):
            raise NotImplementedError

        def is_available(self) -> bool:
            return True

    class MockBroker(AbstractBroker):
        def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
            return BrokerOrder.create_synthetic_ack(request, "MOCK-1")

        def get_order(self, client_order_id=None, broker_order_id=None):  # noqa: ARG002
            return None

        def get_open_orders(self):
            return ()

        def get_positions(self):
            return ()

        def cancel_order(self, client_order_id: str):
            raise NotImplementedError

        def is_available(self) -> bool:
            return True

    cfg_live = DeploymentConfig(
        environment=DeploymentEnvironment.LIVE,
        live_authorized=True,
        credential_source="env",
        runtime_root=tmp_path,
    )

    # 1. SimulatedBroker rejected at construction time
    with pytest.raises(
        DeploymentSafetyError,
        match="LIVE environment cannot be paired with a non-live broker",
    ):
        DeploymentBrokerGuard(delegate=SimulatedBroker(), config=cfg_live)

    # 2. MockBroker rejected at construction time
    with pytest.raises(
        DeploymentSafetyError,
        match="LIVE environment cannot be paired with a non-live broker",
    ):
        DeploymentBrokerGuard(delegate=MockBroker(), config=cfg_live)

    # 3. Also verified in readiness diagnostics
    checker_sim = DeploymentReadinessChecker(config=cfg_live, broker=SimulatedBroker())
    assert checker_sim.check_readiness().broker_ready is False

    checker_mock = DeploymentReadinessChecker(config=cfg_live, broker=MockBroker())
    assert checker_mock.check_readiness().broker_ready is False
