"""
AlphaForge Abstract Broker Interface.
Defines the clean, vendor-independent protocol for order routing, querying,
cancellation, and position retrieval.
"""

from abc import ABC, abstractmethod

from alphaforge.broker.models import (
    BrokerOrder,
    BrokerOrderRequest,
    BrokerPosition,
)


class AbstractBroker(ABC):
    """
    Abstract interface defining operations required from an execution broker.
    Contains no vendor-specific logic. All implementations must be thread-safe.
    """

    @abstractmethod
    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        """
        Submit an order request to the broker.
        Enforces duplicate prevention on client_order_id.
        """
        ...

    @abstractmethod
    def get_order(
        self,
        client_order_id: str | None = None,
        broker_order_id: str | None = None,
    ) -> BrokerOrder | None:
        """
        Retrieve order by client_order_id or broker_order_id.
        Returns None if order is not found on broker order book.
        """
        ...

    @abstractmethod
    def get_open_orders(self) -> tuple[BrokerOrder, ...]:
        """
        Retrieve all currently open (non-terminal) orders from the broker.
        """
        ...

    @abstractmethod
    def get_positions(self) -> tuple[BrokerPosition, ...]:
        """
        Retrieve all active positions reported by the broker.
        """
        ...

    @abstractmethod
    def cancel_order(self, client_order_id: str) -> BrokerOrder:
        """
        Cancel a resting open order by client_order_id.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """
        Check whether the broker service is reachable and operational.
        """
        ...
