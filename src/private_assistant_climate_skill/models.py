import re

from pydantic import field_validator
from sqlmodel import Field, SQLModel

MQTT_TOPIC_REGEX = re.compile(r"[\$#\+\s\0-\31]+")
MAX_TOPIC_LENGTH = 128


class SQLModelValidation(SQLModel):
    model_config = {"from_attributes": True, "validate_assignment": True}


class ClimateSkillDevice(SQLModelValidation, table=True):
    id: int | None = Field(default=None, primary_key=True)
    topic: str
    alias: str
    room: str
    payload_set_template: str = '{"occupied_heating_setpoint": {{ temperature }}}'

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, value: str):
        if MQTT_TOPIC_REGEX.findall(value):
            raise ValueError("Topic must not contain invalid characters.")
        if len(value) > MAX_TOPIC_LENGTH:
            raise ValueError(f"Topic length exceeds maximum allowed limit ({MAX_TOPIC_LENGTH} characters).")
        return value.strip()
