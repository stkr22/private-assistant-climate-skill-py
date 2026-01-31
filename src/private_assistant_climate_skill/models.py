"""Data models for climate skill devices."""

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, field_validator

if TYPE_CHECKING:
    from private_assistant_commons.database import GlobalDevice

MQTT_TOPIC_REGEX = re.compile(r"[\$#\+\s\0-\31]+")
MAX_TOPIC_LENGTH = 128


class ClimateSkillDevice(BaseModel):
    """Skill-specific device representation for climate/HVAC devices.

    Converts from global device registry to skill-specific format with validation.
    Stores MQTT topic and payload template in device_attributes.
    """

    topic: str
    alias: str
    room: str
    payload_set_template: str = '{"occupied_heating_setpoint": {{ temperature }}}'

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, value: str):
        """Validate MQTT topic for invalid characters and length.

        Args:
            value: MQTT topic string to validate

        Returns:
            Stripped and validated topic string

        Raises:
            ValueError: If topic contains invalid characters or exceeds length limit

        """
        if MQTT_TOPIC_REGEX.findall(value):
            raise ValueError("Topic must not contain invalid characters.")
        if len(value) > MAX_TOPIC_LENGTH:
            raise ValueError(f"Topic length exceeds maximum allowed limit ({MAX_TOPIC_LENGTH} characters).")
        return value.strip()

    @classmethod
    def from_global_device(cls, global_device: "GlobalDevice") -> "ClimateSkillDevice":
        """Transform GlobalDevice to ClimateSkillDevice with type safety.

        Args:
            global_device: Device from global registry

        Returns:
            ClimateSkillDevice with skill-specific fields

        Raises:
            ValueError: If required device_attributes are missing

        """
        attrs = global_device.device_attributes or {}

        if not attrs.get("topic"):
            raise ValueError(f"Device {global_device.name} missing required 'topic' in device_attributes")

        return cls(
            topic=attrs["topic"],
            alias=global_device.name,
            room=global_device.room.name if global_device.room else "",
            payload_set_template=attrs.get("payload_set_template", '{"occupied_heating_setpoint": {{ temperature }}}'),
        )
