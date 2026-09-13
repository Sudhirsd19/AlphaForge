"""
AlphaForge Security Authorizer & Secure Broker.

Enforces pre-trade security checks, kill switch guards, dual live authorization,
and reconciliation gates on every order submission without modifying frozen domain logic.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from alphaforge.broker.interface import AbstractBroker

if TYPE_CHECKING:
    from alphaforge.broker.models import (
        BrokerOrder,
        BrokerOrderRequest,
        BrokerPosition,
    )
    from alphaforge.reconciliation.gate import ReconciliationGate
    from alphaforge.security.config import SecurityConfig
    from alphaforge.security.startup import SecurityStartupGate

from alphaforge.security.credentials import CredentialStore
from alphaforge.security.enums import TradingMode
from alphaforge.security.exceptions import (
    KillSwitchEngagedError,
    ReconciliationGateClosedError,
    SecurityAuthorizationError,
)
from alphaforge.security.kill_switch import KillSwitch


class SecurityAuthorizer:
    """
    Evaluates runtime security conditions before allowing orders into broker queues.

    Guarantees:
    - Kill switch engagement blocks new order submissions.
    - LIVE mode submissions require dual-opt-in and authentic live credentials.
    - Closed or missing reconciliation gates block trade entry.
    - Unverified or missing startup security gates block trade entry.
    """

    def __init__(
        self,
        security_config: SecurityConfig,
        credential_store: CredentialStore | None = None,
        kill_switch: KillSwitch | None = None,
        reconciliation_gate: ReconciliationGate | None = None,
        startup_gate: SecurityStartupGate | None = None,
    ) -> None:
        self._config = security_config
        self._credential_store = credential_store or CredentialStore()
        init_status = security_config.kill_switch_initial_status
        self._kill_switch = kill_switch or KillSwitch(initial_status=init_status)
        self._reconciliation_gate = reconciliation_gate
        self._startup_gate = startup_gate

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill_switch

    @property
    def reconciliation_gate(self) -> ReconciliationGate | None:
        return self._reconciliation_gate

    @property
    def startup_gate(self) -> SecurityStartupGate | None:
        return self._startup_gate

    @property
    def config(self) -> SecurityConfig:
        return self._config

    def authorize_order(self, request: BrokerOrderRequest) -> None:
        """
        Authorize an order request against all security constraints.

        Fails closed with SecurityAuthorizationError (or subclass) if any condition is breached.
        """
        cid = request.client_order_id
        mode = self._config.trading_mode_config.effective_mode

        try:
            self._evaluate_security_guards(cid, mode)
        except Exception as exc:
            with contextlib.suppress(Exception):
                self._notify_security_rejection(cid, request.symbol, mode.value, exc)
            raise

        with contextlib.suppress(Exception):
            self._notify_security_acceptance(cid, request.symbol, mode.value)

    def _notify_security_rejection(
        self, cid: str, symbol: str, mode_val: str, exc: Exception
    ) -> None:
        with contextlib.suppress(Exception):
            from alphaforge.observability.hub import observe_security_evaluation

            observe_security_evaluation(
                client_order_id=cid,
                symbol=symbol,
                mode_val=mode_val,
                exc=exc,
            )

    def _notify_security_acceptance(self, cid: str, symbol: str, mode_val: str) -> None:
        with contextlib.suppress(Exception):
            from alphaforge.observability.hub import observe_security_evaluation

            observe_security_evaluation(
                client_order_id=cid,
                symbol=symbol,
                mode_val=mode_val,
            )

    def _evaluate_security_guards(self, cid: str, mode: TradingMode) -> None:
        # 1. Kill Switch Check (Immediate fail closed)
        if self._kill_switch.is_engaged():
            reason = self._kill_switch.latest_reason
            raise KillSwitchEngagedError(
                f"Order {cid} rejected: Kill switch is ENGAGED ({reason})."
            )

        # 2. Live Trading Dual Authorization Check
        if mode == TradingMode.LIVE:
            if not self._config.trading_mode_config.is_live_authorized:
                msg = f"Order {cid} rejected: LIVE mode is not authorized (dual opt-in failed)."
                raise SecurityAuthorizationError(msg)

            # Credential check
            if not self._credential_store.has_live_credentials:
                msg = (
                    f"Order {cid} rejected: Live credentials are not configured in CredentialStore."
                )
                raise SecurityAuthorizationError(msg)

            live_creds = self._credential_store.get_credentials(TradingMode.LIVE)
            if not live_creds.is_live:
                msg = (
                    f"Order {cid} rejected: "
                    f"CredentialStore contains non-live credentials for LIVE mode."
                )
                raise SecurityAuthorizationError(msg)

        # 3. Reconciliation Gate Check (Mandatory for LIVE or when configured)
        if mode == TradingMode.LIVE or self._config.enforce_reconciliation_gate:
            if self._reconciliation_gate is None:
                ctx = "for LIVE mode" if mode == TradingMode.LIVE else "by configuration"
                msg = f"Order {cid} rejected: Reconciliation gate is required {ctx} but is missing."
                raise SecurityAuthorizationError(msg)

            if not self._reconciliation_gate.is_open:
                msg = (
                    f"Order {cid} rejected: "
                    f"Reconciliation gate is closed ({self._reconciliation_gate.reason})."
                )
                raise ReconciliationGateClosedError(msg)

        # 4. Startup Gate Check (Mandatory for LIVE or when configured)
        if mode == TradingMode.LIVE or self._config.enforce_startup_gate:
            if self._startup_gate is None:
                ctx = "for LIVE mode" if mode == TradingMode.LIVE else "by configuration"
                msg = f"Order {cid} rejected: Startup gate is required {ctx} but is missing."
                raise SecurityAuthorizationError(msg)

            if not self._startup_gate.is_verified:
                msg = f"Order {cid} rejected: Startup security gate verification has not passed."
                raise SecurityAuthorizationError(msg)


class SecureBroker(AbstractBroker):
    """
    Secure decorator around AbstractBroker.

    Intercepts order submissions to enforce SecurityAuthorizer checks before
    delegating to the underlying broker (e.g. PaperBroker).
    """

    def __init__(
        self,
        delegate: AbstractBroker,
        authorizer: SecurityAuthorizer,
    ) -> None:
        self._delegate = delegate
        self._authorizer = authorizer

    @property
    def delegate(self) -> AbstractBroker:
        return self._delegate

    @property
    def authorizer(self) -> SecurityAuthorizer:
        return self._authorizer

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        """
        Authorize order request through security policies before routing to broker.
        """
        self._authorizer.authorize_order(request)
        with contextlib.suppress(Exception):
            self._notify_submit(request)

        try:
            order = self._delegate.submit_order(request)
        except Exception as exc:
            with contextlib.suppress(Exception):
                self._notify_submit_failure(request, exc)
            raise

        with contextlib.suppress(Exception):
            self._notify_ack(order)
        return order

    def _notify_submit(self, request: BrokerOrderRequest) -> None:
        with contextlib.suppress(Exception):
            from alphaforge.observability.hub import observe_order_submission

            observe_order_submission(request)

    def _notify_ack(self, order: BrokerOrder) -> None:
        with contextlib.suppress(Exception):
            from alphaforge.observability.hub import observe_order_ack

            observe_order_ack(order)

    def _notify_submit_failure(self, request: BrokerOrderRequest, exc: Exception) -> None:
        with contextlib.suppress(Exception):
            from alphaforge.observability.hub import observe_order_submit_failure

            observe_order_submit_failure(request, exc)

    def get_order(
        self,
        client_order_id: str | None = None,
        broker_order_id: str | None = None,
    ) -> BrokerOrder | None:
        return self._delegate.get_order(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
        )

    def get_open_orders(self) -> tuple[BrokerOrder, ...]:
        return self._delegate.get_open_orders()

    def get_positions(self) -> tuple[BrokerPosition, ...]:
        return self._delegate.get_positions()

    def cancel_order(self, client_order_id: str) -> BrokerOrder:
        # Order cancellation is permitted even if kill switch is engaged
        # (to facilitate emergency position/order unwind)
        return self._delegate.cancel_order(client_order_id)

    def is_available(self) -> bool:
        return self._delegate.is_available()
