from .base import Broker, BrokerError, Order, Fill
from .paper import PaperBroker
from .kabu import KabuBroker


def make_broker(cfg, account=None) -> Broker:
    kind = cfg["broker"].get("type", "paper")
    if kind == "kabu":
        return KabuBroker(cfg["broker"]["kabu"])
    return PaperBroker(account)
