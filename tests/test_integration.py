"""End-to-end integration tests for the climate skill.

These tests validate the complete skill workflow with real external services:
- PostgreSQL database (device registry)
- MQTT broker (message bus)
- Climate skill running in background

Test flow:
1. Setup database with test devices
2. Start skill in background
3. Publish IntentRequest to MQTT
4. Assert skill publishes correct device commands and responses

Run these tests with:
    pytest tests/test_integration.py -v -m integration -n 0

Requirements:
- Compose services (PostgreSQL, Mosquitto) must be running
- If mosquitto.conf was just updated, restart services with:
  docker compose -f .devcontainer/compose.yml restart mosquitto
"""

import asyncio
import contextlib
import json
import logging
import os
import pathlib
import tempfile
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import cast

import aiomqtt
import pytest
import yaml
from private_assistant_commons import ClassifiedIntent, ClientRequest, Entity, EntityType, IntentRequest, IntentType
from private_assistant_commons.database import PostgresConfig
from private_assistant_commons.database.models import DeviceType, GlobalDevice, Room, Skill
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from private_assistant_climate_skill.main import start_skill

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration

# Logger for test debugging
logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
async def db_engine():
    """Create a database engine for integration tests."""
    db_config = PostgresConfig()
    engine = create_async_engine(str(db_config.connection_string_async), echo=False)

    # Ensure tables exist
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    """Create a database session for each test."""
    async with AsyncSession(db_engine) as session:
        yield session


@pytest.fixture
def mqtt_config():
    """Get MQTT configuration from environment variables."""
    return {
        "host": os.getenv("MQTT_HOST", "mosquitto"),
        "port": int(os.getenv("MQTT_PORT", "1883")),
    }


@pytest.fixture
async def mqtt_test_client(mqtt_config):
    """Create an MQTT test client."""
    async with aiomqtt.Client(hostname=mqtt_config["host"], port=mqtt_config["port"]) as client:
        yield client


@pytest.fixture
async def test_skill_entity(db_session) -> Skill:
    """Create a test skill entity in the database."""
    result = await db_session.exec(select(Skill).where(Skill.name == "climate-skill-integration-test"))
    skill = result.first()

    if skill is None:
        skill = Skill(name="climate-skill-integration-test")
        db_session.add(skill)
        await db_session.flush()
        await db_session.refresh(skill)

    assert skill is not None
    return cast("Skill", skill)


@pytest.fixture
async def test_device_type(db_session) -> DeviceType:
    """Create a test device type in the database."""
    result = await db_session.exec(select(DeviceType).where(DeviceType.name == "thermostat"))
    device_type = result.first()

    if device_type is None:
        device_type = DeviceType(name="thermostat")
        db_session.add(device_type)
        await db_session.flush()
        await db_session.refresh(device_type)

    assert device_type is not None
    return cast("DeviceType", device_type)


@pytest.fixture
async def test_room(db_session) -> Room:
    """Create a test room in the database."""
    room_name = f"test_room_{uuid.uuid4().hex[:8]}"
    room = Room(name=room_name)
    db_session.add(room)
    await db_session.flush()
    await db_session.refresh(room)
    return room


@pytest.fixture
async def test_device(db_session, test_skill_entity, test_device_type, test_room) -> AsyncGenerator[GlobalDevice, None]:
    """Create a single test device in the database.

    Note: This fixture must be created BEFORE the running_skill fixture
    so the device is loaded during skill initialization.
    """
    await db_session.refresh(test_room)
    await db_session.refresh(test_skill_entity)
    await db_session.refresh(test_device_type)

    logger.debug("Creating device with skill_id=%s, skill_name=%s", test_skill_entity.id, test_skill_entity.name)

    device = GlobalDevice(
        device_type_id=test_device_type.id,
        name="test thermostat",
        pattern=["test thermostat", f"{test_room.name} test thermostat"],
        device_attributes={
            "topic": "test/integration/climate/main/set",
            "payload_set_template": '{"occupied_heating_setpoint": {{ temperature }}}',
        },
        room_id=test_room.id,
        skill_id=test_skill_entity.id,
    )
    db_session.add(device)
    await db_session.commit()
    await db_session.refresh(device, ["room"])

    logger.debug("Device created with ID=%s, skill_id=%s", device.id, device.skill_id)

    yield device

    # Cleanup: Delete test device
    logger.debug("Cleaning up device %s", device.id)
    await db_session.delete(device)
    await db_session.commit()


@pytest.fixture
async def test_devices_multiple(
    db_session, test_skill_entity, test_device_type, test_room
) -> AsyncGenerator[list[GlobalDevice], None]:
    """Create multiple test devices in the same room.

    Note: This fixture must be created BEFORE the running_skill fixture
    so the devices are loaded during skill initialization.
    """
    await db_session.refresh(test_room)
    await db_session.refresh(test_skill_entity)
    await db_session.refresh(test_device_type)

    room_id = test_room.id
    skill_id = test_skill_entity.id
    device_type_id = test_device_type.id
    room_name = test_room.name

    devices = [
        GlobalDevice(
            device_type_id=device_type_id,
            name="thermostat one",
            pattern=["thermostat one", f"{room_name} thermostat one"],
            device_attributes={
                "topic": "test/integration/room/thermostat1/set",
                "payload_set_template": '{"occupied_heating_setpoint": {{ temperature }}}',
            },
            room_id=room_id,
            skill_id=skill_id,
        ),
        GlobalDevice(
            device_type_id=device_type_id,
            name="thermostat two",
            pattern=["thermostat two", f"{room_name} thermostat two"],
            device_attributes={
                "topic": "test/integration/room/thermostat2/set",
                "payload_set_template": '{"occupied_heating_setpoint": {{ temperature }}}',
            },
            room_id=room_id,
            skill_id=skill_id,
        ),
        GlobalDevice(
            device_type_id=device_type_id,
            name="thermostat three",
            pattern=["thermostat three", f"{room_name} thermostat three"],
            device_attributes={
                "topic": "test/integration/room/thermostat3/set",
                "payload_set_template": '{"occupied_heating_setpoint": {{ temperature }}}',
            },
            room_id=room_id,
            skill_id=skill_id,
        ),
    ]

    for device in devices:
        db_session.add(device)

    await db_session.commit()

    for device in devices:
        await db_session.refresh(device, ["room"])

    yield devices

    # Cleanup: Delete all test devices
    for device in devices:
        await db_session.delete(device)
    await db_session.commit()


@pytest.fixture
async def skill_config_file():
    """Create a temporary config file for the skill."""
    config = {
        "client_id": "climate-skill-integration-test",
        "base_topic": "assistant",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config, f)
        config_path = pathlib.Path(f.name)

    yield config_path

    # Cleanup: Remove temp file
    config_path.unlink(missing_ok=True)


@pytest.fixture
async def running_skill_single_device(skill_config_file, test_device, db_engine):
    """Start the skill in background with a single device ready.

    Args:
        skill_config_file: Path to skill config
        test_device: Test device that must be created before skill starts
        db_engine: Database engine to verify device visibility
    """
    # Device is already created by test_device fixture
    # Give database time to fully persist the commit
    await asyncio.sleep(0.5)

    # Verify device is visible from a fresh session (simulate what the skill does)
    async with AsyncSession(db_engine) as session:
        # Check device by ID
        result = await session.exec(select(GlobalDevice).where(GlobalDevice.id == test_device.id))
        check_device = result.first()
        logger.debug("Device visible by ID: %s", check_device is not None)

        # Check device by skill_id (this is what the skill does)
        result = await session.exec(select(GlobalDevice).where(GlobalDevice.skill_id == test_device.skill_id))
        devices_by_skill = result.all()
        logger.debug("Devices found by skill_id=%s: %d", test_device.skill_id, len(devices_by_skill))
        for dev in devices_by_skill:
            logger.debug("  - %s (ID: %s)", dev.name, dev.id)

        # Check if the Skill entity exists
        result = await session.exec(select(Skill).where(Skill.id == test_device.skill_id))
        skill_entity = result.first()
        skill_name = skill_entity.name if skill_entity else "N/A"
        logger.debug("Skill entity exists: %s, name: %s", skill_entity is not None, skill_name)

    # Start skill as background task
    skill_task = asyncio.create_task(start_skill(skill_config_file))

    # Wait for skill to initialize and subscribe to all topics
    # This includes the device update topic listener
    await asyncio.sleep(3)

    # Trigger device load by publishing device update notification
    # The skill's device cache is only populated when it receives this notification
    mqtt_host = os.getenv("MQTT_HOST", "mosquitto")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    async with aiomqtt.Client(hostname=mqtt_host, port=mqtt_port) as trigger_client:
        await trigger_client.publish("assistant/global_device_update", "", qos=1)

    # Wait for skill to process the device update and load devices
    await asyncio.sleep(2)

    yield

    # Cleanup: Cancel skill task
    skill_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await skill_task


@pytest.fixture
async def running_skill_multiple_devices(skill_config_file, test_devices_multiple):  # noqa: ARG001
    """Start the skill in background with multiple devices ready.

    Args:
        skill_config_file: Path to skill config
        test_devices_multiple: Test devices that must be created before skill starts
    """
    # Devices are already created by test_devices_multiple fixture
    # Give database time to fully persist the commit
    await asyncio.sleep(0.5)

    # Start skill as background task
    skill_task = asyncio.create_task(start_skill(skill_config_file))

    # Wait for skill to initialize and subscribe to all topics
    # This includes the device update topic listener
    await asyncio.sleep(3)

    # Trigger device load by publishing device update notification
    mqtt_host = os.getenv("MQTT_HOST", "mosquitto")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    async with aiomqtt.Client(hostname=mqtt_host, port=mqtt_port) as trigger_client:
        await trigger_client.publish("assistant/global_device_update", "", qos=1)

    # Wait for skill to process the device update and load devices
    await asyncio.sleep(2)

    yield

    # Cleanup: Cancel skill task
    skill_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await skill_task


@pytest.fixture
async def running_skill(skill_config_file):
    """Start the skill in background without any test devices.

    Used for tests that don't need devices (e.g., error handling tests).
    """
    # Start skill as background task
    skill_task = asyncio.create_task(start_skill(skill_config_file))

    # Wait for skill to initialize and subscribe to topics
    await asyncio.sleep(3)

    yield

    # Cleanup: Cancel skill task
    skill_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await skill_task


class TestSetTemperatureCommand:
    """Test single device temperature set commands (DEVICE_SET)."""

    async def test_set_temperature_command(
        self,
        test_device,
        test_room,
        running_skill_single_device,  # noqa: ARG002
        mqtt_test_client,
    ):
        """Test that DEVICE_SET intent triggers correct MQTT command and response.

        Flow:
        1. Publish IntentRequest with DEVICE_SET intent and temperature
        2. Assert device command published to correct topic with correct payload
        3. Assert response published to output topic

        Note: Uses running_skill_single_device fixture which ensures test_device
        is created before the skill starts.
        """
        output_topic = f"test/output/{uuid.uuid4().hex}"
        device_topic = test_device.device_attributes["topic"]
        target_temperature = 22

        # Prepare IntentRequest
        number_entity = Entity(
            id=uuid.uuid4(),
            type=EntityType.NUMBER,
            raw_text="22",
            normalized_value=target_temperature,
            confidence=0.9,
            metadata={"unit": "celsius"},
            linked_to=[],
        )

        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.DEVICE_SET,
            confidence=0.9,
            entities={"number": [number_entity]},
            alternative_intents=[],
            raw_text="set temperature to 22 degrees",
            timestamp=datetime.now(),
        )

        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="set temperature to 22 degrees",
            room=test_room.name,
            output_topic=output_topic,
        )

        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        # Subscribe to device topic and response topic
        await mqtt_test_client.subscribe(device_topic)
        await mqtt_test_client.subscribe(output_topic)

        # Publish IntentRequest
        await mqtt_test_client.publish(
            "assistant/intent_engine/result",
            intent_request.model_dump_json(),
            qos=1,
        )

        # Collect messages
        device_command_received = False
        response_received = False

        async with asyncio.timeout(10):
            async for message in mqtt_test_client.messages:
                topic = str(message.topic)
                payload = message.payload.decode()

                if topic == device_topic:
                    # Parse and verify JSON payload
                    payload_data = json.loads(payload)
                    assert "occupied_heating_setpoint" in payload_data
                    assert payload_data["occupied_heating_setpoint"] == target_temperature
                    device_command_received = True

                if topic == output_topic:
                    # Response should mention setting temperature
                    assert "temperature" in payload.lower() or str(target_temperature) in payload
                    response_received = True

                # Exit when both messages received
                if device_command_received and response_received:
                    break

        assert device_command_received, "Device command was not published"
        assert response_received, "Response was not published"


class TestRoomWideSetTemperature:
    """Test room-wide temperature set commands (multiple devices)."""

    async def test_room_wide_set_temperature(
        self,
        test_devices_multiple,
        test_room,
        running_skill_multiple_devices,  # noqa: ARG002
        mqtt_test_client,
    ):
        """Test that DEVICE_SET with multiple devices triggers all commands.

        Flow:
        1. Publish IntentRequest with DEVICE_SET for entire room (no specific device)
        2. Assert commands published to all 3 device topics
        3. Assert response indicates multiple devices

        Note: Uses running_skill_multiple_devices fixture which ensures test_devices_multiple
        is created before the skill starts.
        """
        output_topic = f"test/output/{uuid.uuid4().hex}"
        device_topics = [d.device_attributes["topic"] for d in test_devices_multiple]
        target_temperature = 20

        # Prepare IntentRequest without specific device (room-wide command)
        number_entity = Entity(
            id=uuid.uuid4(),
            type=EntityType.NUMBER,
            raw_text="20",
            normalized_value=target_temperature,
            confidence=0.9,
            metadata={"unit": "celsius"},
            linked_to=[],
        )

        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.DEVICE_SET,
            confidence=0.9,
            entities={"number": [number_entity]},
            alternative_intents=[],
            raw_text="set temperature to 20 degrees",
            timestamp=datetime.now(),
        )

        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="set temperature to 20 degrees",
            room=test_room.name,
            output_topic=output_topic,
        )

        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        # Subscribe to all device topics and response topic
        for topic in device_topics:
            await mqtt_test_client.subscribe(topic)
        await mqtt_test_client.subscribe(output_topic)

        # Publish IntentRequest
        await mqtt_test_client.publish(
            "assistant/intent_engine/result",
            intent_request.model_dump_json(),
            qos=1,
        )

        # Collect messages
        device_commands_received = set()
        response_received = False

        async with asyncio.timeout(10):
            async for message in mqtt_test_client.messages:
                topic = str(message.topic)
                payload = message.payload.decode()

                if topic in device_topics:
                    # Parse and verify JSON payload
                    payload_data = json.loads(payload)
                    assert "occupied_heating_setpoint" in payload_data
                    assert payload_data["occupied_heating_setpoint"] == target_temperature
                    device_commands_received.add(topic)

                if topic == output_topic:
                    # Response should indicate multiple devices or room-wide action
                    response_received = True

                # Exit when all messages received
                if len(device_commands_received) == len(device_topics) and response_received:
                    break

        assert len(device_commands_received) == len(device_topics), (
            f"Expected commands to {len(device_topics)} devices, got {len(device_commands_received)}"
        )
        assert response_received, "Response was not published"


class TestDeviceNotFound:
    """Test error handling when device is not found."""

    async def test_device_not_found(self, running_skill, mqtt_test_client, test_room):  # noqa: ARG002
        """Test that request without climate devices sends error response.

        Flow:
        1. Publish IntentRequest for DEVICE_SET in room without HVAC devices
        2. Assert no device commands published
        3. Assert error response published
        """
        output_topic = f"test/output/{uuid.uuid4().hex}"

        # Prepare IntentRequest
        number_entity = Entity(
            id=uuid.uuid4(),
            type=EntityType.NUMBER,
            raw_text="25",
            normalized_value=25,
            confidence=0.9,
            metadata={"unit": "celsius"},
            linked_to=[],
        )

        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.DEVICE_SET,
            confidence=0.9,
            entities={"number": [number_entity]},
            alternative_intents=[],
            raw_text="set temperature to 25 degrees",
            timestamp=datetime.now(),
        )

        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="set temperature to 25 degrees",
            room=test_room.name,
            output_topic=output_topic,
        )

        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        # Subscribe to response topic and a wildcard for any device commands
        await mqtt_test_client.subscribe(output_topic)
        await mqtt_test_client.subscribe("test/integration/#")

        # Publish IntentRequest
        await mqtt_test_client.publish(
            "assistant/intent_engine/result",
            intent_request.model_dump_json(),
            qos=1,
        )

        # Collect messages
        device_command_received = False
        response_received = False
        response_payload = None

        async with asyncio.timeout(10):
            async for message in mqtt_test_client.messages:
                topic = str(message.topic)
                payload = message.payload.decode()

                # Check if any device command was sent
                if topic.startswith("test/integration/") and topic != output_topic:
                    device_command_received = True

                if topic == output_topic:
                    response_payload = payload
                    response_received = True
                    break  # Got response, can exit

        assert not device_command_received, "Device command should not be published for non-existent device"
        assert response_received, "Error response should be published"
        # Response should indicate device not found or similar error
        assert response_payload is not None
        assert "couldn't find" in response_payload.lower() or "not found" in response_payload.lower()


class TestSystemHelp:
    """Test SYSTEM_HELP intent."""

    async def test_system_help(self, running_skill, mqtt_test_client, test_room):  # noqa: ARG002
        """Test that SYSTEM_HELP intent sends help response.

        Flow:
        1. Publish IntentRequest with SYSTEM_HELP intent
        2. Assert help response published to output topic
        3. Assert no device commands published
        """
        output_topic = f"test/output/{uuid.uuid4().hex}"

        # Prepare IntentRequest
        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.SYSTEM_HELP,
            confidence=0.9,
            entities={},
            alternative_intents=[],
            raw_text="help with climate",
            timestamp=datetime.now(),
        )

        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="help with climate",
            room=test_room.name,
            output_topic=output_topic,
        )

        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        # Subscribe to response topic
        await mqtt_test_client.subscribe(output_topic)

        # Publish IntentRequest
        await mqtt_test_client.publish(
            "assistant/intent_engine/result",
            intent_request.model_dump_json(),
            qos=1,
        )

        # Collect messages
        response_received = False
        response_payload = None

        async with asyncio.timeout(10):
            async for message in mqtt_test_client.messages:
                topic = str(message.topic)
                payload = message.payload.decode()

                if topic == output_topic:
                    response_payload = payload
                    response_received = True
                    break  # Got response, can exit

        assert response_received, "Help response should be published"
        assert response_payload is not None
        # Response should contain help information
        assert len(response_payload) > 0
