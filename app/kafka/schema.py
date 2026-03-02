from datetime import datetime

from pydantic import BaseModel


class KafkaMessageSchema(BaseModel):
    """Schema for incoming Kafka messages.

    All consumers expect messages in this format.
    """

    event_name: str
    timestamp: datetime
    payload: dict | list
