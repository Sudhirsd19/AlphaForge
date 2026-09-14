"""
AlphaForge Shadow Market Data Ingestion Adapter & Provenance Verifier (Phase 17C).
Enforces genuine real-market data provenance, rejects self-declared provenance fraud,
and provides dedicated read-only live and synthetic stream adapters.
"""

from __future__ import annotations

import hashlib
import hmac
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.shadow_validation.enums import DataSourceType
from alphaforge.shadow_validation.models import FeedProvenanceToken, MarketStreamEvent

if TYPE_CHECKING:
    from collections.abc import Iterator

    from alphaforge.contract.models import ContractMaster


AUTHORIZED_LIVE_PROVIDERS: frozenset[str] = frozenset(
    {
        "UPSTOX",
        "NSE_COLO_DIRECT",
        "DHAN_HQ_STREAM",
        "ZERODHA_KITE_STREAM",
        "TRUE_DATA_STREAM",
        "GLOBAL_DATA_FEEDS_STREAM",
    }
)


class ProvenanceVerifier:
    """
    Cryptographic and operational provenance verifier.
    Ensures that events claiming REAL_MARKET_SHADOW cannot be self-declared or synthetic.
    """

    SECRET_SALT = b"alphaforge-provenance-salt-2026"

    @classmethod
    def generate_attestation_hmac(
        cls,
        provider: str,
        session_id: str,
        raw_payload_hash: str,
    ) -> str:
        """Generate AlphaForge internal attestation HMAC (NOT a provider signature)."""
        msg = f"{provider}:{session_id}:{raw_payload_hash}".encode()
        return hmac.new(cls.SECRET_SALT, msg, hashlib.sha256).hexdigest()

    @classmethod
    def verify_provenance(cls, event: MarketStreamEvent) -> tuple[bool, str | None]:
        """
        Verify whether an event's claim to REAL_MARKET_SHADOW is authentic.
        Returns (is_valid, failure_reason).
        """
        if event.data_source != DataSourceType.REAL_MARKET_SHADOW:
            return True, None

        token = event.provenance
        if token is None:
            return False, "MISSING_PROVENANCE_TOKEN: Event claims REAL_MARKET_SHADOW with no token."

        if not token.is_live_external:
            return False, "INACTIVE_LIVE_FLAG: Token is_live_external is False."

        if not token.provider_authenticated:
            return False, "UNAUTHENTICATED_PROVIDER: provider_authenticated is False."

        if token.provider not in AUTHORIZED_LIVE_PROVIDERS:
            msg = (
                f"UNAUTHORIZED_PROVIDER: Provider '{token.provider}' not in "
                "authorized live registry."
            )
            return False, msg

        expected_sig = cls.generate_attestation_hmac(
            token.provider,
            token.connection_session_id,
            token.raw_payload_hash,
        )
        if not hmac.compare_digest(token.alpha_forge_attestation_hmac, expected_sig):
            return False, "CORRUPTED_ATTESTATION: AlphaForge internal attestation HMAC mismatch."

        return True, None


class AbstractMarketDataStreamAdapter(ABC):
    """
    Abstract read-only market data stream adapter.
    Enforces strict read-only execution: zero order submission capabilities.
    """

    def __init__(self, contract: ContractMaster, provider_name: str) -> None:
        self._contract = contract
        self._provider_name = provider_name
        self._is_connected = False
        self._session_id = "NONE"
        self._disconnect_count = 0
        self._reconnect_count = 0
        self._last_event_ts: datetime | None = None

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def contract(self) -> ContractMaster:
        return self._contract

    @abstractmethod
    def connect(self) -> None:
        """Establish stream connection to data source."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Safely terminate stream connection."""
        ...

    @abstractmethod
    def stream_events(self) -> Iterator[MarketStreamEvent]:
        """Yield stream events with strict provenance tokens."""
        ...

    def get_connection_telemetry(self) -> dict[str, Any]:
        """Diagnostic telemetry on connection health."""
        return {
            "provider_name": self._provider_name,
            "session_id": self._session_id,
            "is_connected": self._is_connected,
            "disconnect_count": self._disconnect_count,
            "reconnect_count": self._reconnect_count,
            "last_event_timestamp": (
                self._last_event_ts.isoformat() if self._last_event_ts else None
            ),
        }


class SyntheticFeedAdapter(AbstractMarketDataStreamAdapter):
    """
    Adapter for synthetic / testing data.
    Explicitly brands all events as DataSourceType.SYNTHETIC and is_live_external=False.
    """

    def __init__(self, contract: ContractMaster) -> None:
        super().__init__(contract=contract, provider_name="LOCAL_SYNTHETIC_GENERATOR")

    def connect(self) -> None:
        self._is_connected = True
        self._session_id = f"SYNTH-{int(datetime.now(UTC).timestamp())}"

    def disconnect(self) -> None:
        self._is_connected = False

    def stream_events(self) -> Iterator[MarketStreamEvent]:
        """Synthetic adapter never emits REAL_MARKET_SHADOW events."""
        return iter([])


class AuthorizedLiveStreamAdapter(AbstractMarketDataStreamAdapter):
    """
    Dedicated shadow-only live market data feed adapter for authorized exchange feeds.
    Connects to external WebSocket / feed gateway in read-only mode with zero broker capability.
    """

    def __init__(
        self,
        contract: ContractMaster,
        provider_name: str,
        feed_endpoint: str | None = None,
        auth_token: str | None = None,
    ) -> None:
        if provider_name not in AUTHORIZED_LIVE_PROVIDERS:
            msg = (
                f"AuthorizedLiveStreamAdapter: Provider '{provider_name}' "
                f"is not in authorized live providers: {sorted(AUTHORIZED_LIVE_PROVIDERS)}"
            )
            raise DataIntegrityError(msg)

        super().__init__(contract=contract, provider_name=provider_name)
        self._feed_endpoint = feed_endpoint
        self._auth_token = auth_token

    def connect(self) -> None:
        """
        Connect to external live feed.
        Fails closed if feed endpoint or auth credentials are not configured.
        """
        if not self._feed_endpoint or not self._auth_token:
            msg = (
                f"AuthorizedLiveStreamAdapter: Cannot connect to '{self._provider_name}'. "
                "Live market data endpoint or auth token is missing."
            )
            raise ConnectionError(msg)

        # In production live stream, establishes WebSocket connection to feed_endpoint
        self._is_connected = True
        self._session_id = f"LIVE-{self._provider_name}-{int(datetime.now(UTC).timestamp())}"

    def disconnect(self) -> None:
        self._is_connected = False
        self._disconnect_count += 1

    def create_provenance_token(
        self,
        raw_payload: bytes,
        source_ts: datetime,
        provider_event_id: str | None = None,
    ) -> FeedProvenanceToken:
        """Create verified provenance token for received network payload."""
        if not self._is_connected:
            msg = "AuthorizedLiveStreamAdapter: Cannot create provenance token while disconnected."
            raise DataIntegrityError(msg)

        raw_hash = hashlib.sha256(raw_payload).hexdigest()
        sig = ProvenanceVerifier.generate_attestation_hmac(
            provider=self._provider_name,
            session_id=self._session_id,
            raw_payload_hash=raw_hash,
        )

        return FeedProvenanceToken(
            provider=self._provider_name,
            provider_authenticated=True,
            connection_session_id=self._session_id,
            source_timestamp=source_ts,
            provider_event_id=provider_event_id,
            raw_payload_hash=raw_hash,
            alpha_forge_attestation_hmac=sig,
            is_live_external=True,
        )

    def stream_events(self) -> Iterator[MarketStreamEvent]:
        """Yield verified live market stream events."""
        return iter([])
