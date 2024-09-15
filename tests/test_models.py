import pytest
from pydantic import ValidationError

from private_assistant_climate_skill.models import ClimateSkillDevice

# Define test cases with valid and invalid topics
valid_topics = [
    "zigbee2mqtt/livingroom/climate/main",
    "home/automation/climate/bedroom",
    "devices/kitchen/climate",
]

invalid_topics = [
    "zigbee2mqtt/livingroom/climate/main\n",  # Contains newline
    "home/automation/#",  # Contains invalid wildcard
    " devices/kitchen/climate ",  # Contains leading/trailing whitespace
    "invalid\0topic",  # Contains null character
    "home_home/automation_automation/climate_sensor/climate_sensor/sensor_sensor/very_long_topic_exceeding_maximum_length_beyond_128_characters",  # Exceeds max length
]


# Test that valid topics are accepted
@pytest.mark.parametrize("topic", valid_topics)
def test_valid_topics(topic):
    try:
        device = ClimateSkillDevice(topic=topic, alias="Valid Climate", room="Room")
        assert device.topic == topic.strip()  # Ensure the topic is properly accepted and trimmed
    except ValidationError:
        pytest.fail(f"Valid topic '{topic}' was unexpectedly rejected.")


# Test that invalid topics are rejected
@pytest.mark.parametrize("topic", invalid_topics)
def test_invalid_topics(topic):
    with pytest.raises(ValidationError):
        ClimateSkillDevice(topic=topic, alias="Invalid Climate", room="Room")
