"""Narrow adapter around the optional Confluent producer; configuration stays outside Git."""

from collections.abc import Callable

from retailops_ai.intelligence_events.outbox import DeliveryMessage, EventProducer


class ConfluentEventProducer:
    def __init__(self, producer: EventProducer) -> None:
        self.producer = producer

    def produce(
        self,
        topic: str,
        *,
        key: bytes,
        value: bytes,
        on_delivery: Callable[[object, DeliveryMessage], None],
    ) -> None:
        self.producer.produce(
            topic,
            key=key,
            value=value,
            on_delivery=lambda error, message: on_delivery(error, message),
        )

    def flush(self, timeout: float) -> int:
        return self.producer.flush(timeout)
