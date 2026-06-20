from abc import ABC, abstractmethod


class BrokerInterface(ABC):
    @abstractmethod
    def get_account(self):
        raise NotImplementedError

    @abstractmethod
    def get_positions(self):
        raise NotImplementedError

    @abstractmethod
    def get_open_orders(self):
        raise NotImplementedError

    @abstractmethod
    def preview_order(self, order_intent):
        raise NotImplementedError

    def submit_order(self, order_intent):
        raise RuntimeError("Order submission disabled in current build.")
