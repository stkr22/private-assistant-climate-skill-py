import re

from pydantic import ValidationInfo, field_validator
from sqlmodel import Field, SQLModel

MQTT_TOPIC_REGEX = re.compile(r"[\$#\+\s\0-\31]+")


class SQLModelValidation(SQLModel):
    model_config = {"from_attributes": True, "validate_assignment": True}


class ClimateSkillDevice(SQLModelValidation, table=True):  # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    topic: str
    alias: str
    room: str
    payload_set_template: str = '{"occupied_heating_setpoint": {{ temperature }}}'

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, value: str, info: ValidationInfo):
        if MQTT_TOPIC_REGEX.findall(value):
            raise ValueError("Topic must not contain invalid characters.")
        if len(value) > 128:
            raise ValueError("Topic length exceeds maximum allowed limit (128 characters).")
        return value.strip()
