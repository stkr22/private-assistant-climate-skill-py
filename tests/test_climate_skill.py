import logging
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

import jinja2
from private_assistant_commons import ClassifiedIntent, ClientRequest, Entity, EntityType, IntentRequest, IntentType
from private_assistant_commons.database import GlobalDevice, Room

from private_assistant_climate_skill.climate_skill import ClimateSkill, Parameters


class TestClimateSkill(unittest.IsolatedAsyncioTestCase):
    """Test suite for ClimateSkill with new intent-based architecture."""

    async def asyncSetUp(self):
        """Set up test fixtures before each test."""
        # Create mock components for testing
        self.mock_mqtt_client = AsyncMock()
        self.mock_config = Mock()
        self.mock_config.client_id = "climate_skill_test"  # Required by BaseSkill
        self.mock_config.intent_analysis_result_topic = "test/intent"
        self.mock_template_env = Mock(spec=jinja2.Environment)

        # Mock task_group with proper create_task behavior
        self.mock_task_group = Mock()

        def create_mock_task(_coro, **kwargs):  # noqa: ARG001
            mock_task = Mock()
            mock_task.add_done_callback = Mock()
            return mock_task

        self.mock_task_group.create_task = Mock(side_effect=create_mock_task)

        self.mock_logger = Mock(spec=logging.Logger)

        # Create mock templates
        self.mock_help_template = Mock()
        self.mock_help_template.render.return_value = "Help text"
        self.mock_set_template = Mock()
        self.mock_set_template.render.return_value = "Temperature set"

        self.mock_template_env.get_template.side_effect = lambda name: {
            "help.j2": self.mock_help_template,
            "set_temperature.j2": self.mock_set_template,
        }[name]

        # Create mock database engine (not actually used in unit tests)
        self.mock_db_engine = Mock()

        # Create skill instance
        self.skill = ClimateSkill(
            config_obj=self.mock_config,
            mqtt_client=self.mock_mqtt_client,
            db_engine=self.mock_db_engine,
            template_env=self.mock_template_env,
            task_group=self.mock_task_group,
            logger=self.mock_logger,
        )

    def _create_mock_global_device(self, name: str, room_name: str, topic: str, payload_template: str) -> GlobalDevice:
        """Create a mock GlobalDevice for testing.

        Args:
            name: Device name
            room_name: Room name
            topic: MQTT topic
            payload_template: Jinja2 template for MQTT payload

        Returns:
            Mock GlobalDevice object
        """
        mock_room = Mock(spec=Room)
        mock_room.name = room_name

        mock_device = Mock(spec=GlobalDevice)
        mock_device.name = name
        mock_device.room = mock_room
        mock_device.device_attributes = {
            "topic": topic,
            "payload_set_template": payload_template,
        }
        return mock_device

    def _create_mock_entity(self, entity_type: str, raw_text: str, normalized_value: str | int) -> Entity:
        """Create a mock Entity for testing.

        Args:
            entity_type: Type of entity (room, number, etc.)
            raw_text: Original text from user
            normalized_value: Normalized/extracted value

        Returns:
            Entity object
        """
        return Entity(
            type=EntityType(entity_type),
            raw_text=raw_text,
            normalized_value=normalized_value,
            confidence=0.9,
            metadata={},
        )

    async def test_get_devices_from_global_registry(self):
        """Test getting devices from global device registry."""
        # Mock global_devices
        mock_device_1 = self._create_mock_global_device(
            "Living Room Thermostat",
            "livingroom",
            "livingroom/climate/main",
            '{"occupied_heating_setpoint": {{ temperature }}}',
        )
        mock_device_2 = self._create_mock_global_device(
            "Bedroom Thermostat", "bedroom", "bedroom/climate/main", '{"occupied_heating_setpoint": {{ temperature }}}'
        )
        mock_device_3 = self._create_mock_global_device(
            "Kitchen Thermostat", "kitchen", "kitchen/climate/main", '{"occupied_heating_setpoint": {{ temperature }}}'
        )

        self.skill.global_devices = [mock_device_1, mock_device_2, mock_device_3]

        # Fetch devices for "livingroom"
        devices = await self.skill.get_devices(["livingroom"])

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].alias, "Living Room Thermostat")
        self.assertEqual(devices[0].topic, "livingroom/climate/main")
        self.assertEqual(devices[0].room, "livingroom")

        # Fetch devices for "livingroom" and "bedroom"
        devices = await self.skill.get_devices(["livingroom", "bedroom"])

        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0].alias, "Living Room Thermostat")
        self.assertEqual(devices[1].alias, "Bedroom Thermostat")

    async def test_find_parameters_with_temperature(self):
        """Test extracting parameters from classified intent with temperature."""
        # Mock global_devices
        mock_device = self._create_mock_global_device(
            "Living Room Thermostat",
            "livingroom",
            "livingroom/climate/main",
            '{"occupied_heating_setpoint": {{ temperature }}}',
        )
        self.skill.global_devices = [mock_device]

        # Create mock classified intent
        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.95,
            entities={
                "numbers": [self._create_mock_entity("number", "22", 22)],
                "rooms": [self._create_mock_entity("room", "living room", "livingroom")],
            },
            raw_text="set temperature to 22 degrees in living room",
        )

        # Find parameters
        parameters = await self.skill.find_parameters(IntentType.DEVICE_SET, classified_intent, "livingroom")

        self.assertEqual(len(parameters.targets), 1)
        self.assertEqual(parameters.targets[0].alias, "Living Room Thermostat")
        self.assertEqual(parameters.temperature, 22)
        self.assertEqual(parameters.rooms, ["livingroom"])

    async def test_find_parameters_fallback_to_current_room(self):
        """Test that parameters fallback to current room when no room entities."""
        # Mock global_devices
        mock_device = self._create_mock_global_device(
            "Kitchen Thermostat", "kitchen", "kitchen/climate/main", '{"occupied_heating_setpoint": {{ temperature }}}'
        )
        self.skill.global_devices = [mock_device]

        # Create mock classified intent without room entities
        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.95,
            entities={
                "numbers": [self._create_mock_entity("number", "20", 20)],
            },
            raw_text="set temperature to 20 degrees",
        )

        # Find parameters (should use current_room)
        parameters = await self.skill.find_parameters(IntentType.DEVICE_SET, classified_intent, "kitchen")

        self.assertEqual(len(parameters.targets), 1)
        self.assertEqual(parameters.targets[0].alias, "Kitchen Thermostat")
        self.assertEqual(parameters.temperature, 20)
        self.assertEqual(parameters.rooms, ["kitchen"])

    async def test_handle_device_set(self):
        """Test handling DEVICE_SET intent."""
        # Mock global_devices
        mock_device = self._create_mock_global_device(
            "Living Room Thermostat",
            "livingroom",
            "livingroom/climate/main",
            '{"occupied_heating_setpoint": {{ temperature }}}',
        )
        self.skill.global_devices = [mock_device]

        # Create mock intent request
        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="set temperature to 22 degrees",
            room="livingroom",
            output_topic="test/output",
        )

        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.95,
            entities={
                "numbers": [self._create_mock_entity("number", "22", 22)],
            },
            raw_text="set temperature to 22 degrees",
        )

        intent_request = IntentRequest(
            classified_intent=classified_intent,
            client_request=client_request,
        )

        # Mock send_response (runs in background task, not asserted)
        with patch.object(self.skill, "send_response"):
            await self.skill._handle_device_set(intent_request)
            # Verify handler completed without error

    async def test_handle_device_set_no_devices(self):
        """Test DEVICE_SET with no devices found."""
        # Empty global_devices
        self.skill.global_devices = []

        # Create mock intent request
        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="set temperature to 22 degrees",
            room="livingroom",
            output_topic="test/output",
        )

        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.95,
            entities={
                "numbers": [self._create_mock_entity("number", "22", 22)],
            },
            raw_text="set temperature to 22 degrees",
        )

        intent_request = IntentRequest(
            classified_intent=classified_intent,
            client_request=client_request,
        )

        # Mock send_response
        with patch.object(self.skill, "send_response") as mock_send_response:
            await self.skill._handle_device_set(intent_request)

            # Verify error response sent
            mock_send_response.assert_called_once()
            args = mock_send_response.call_args[0]
            self.assertIn("couldn't find", args[0].lower())

    async def test_handle_system_help(self):
        """Test handling SYSTEM_HELP intent."""
        # Create mock intent request
        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="help with climate",
            room="livingroom",
            output_topic="test/output",
        )

        classified_intent = ClassifiedIntent(
            intent_type=IntentType.SYSTEM_HELP,
            confidence=0.85,
            entities={},
            raw_text="help with climate",
        )

        intent_request = IntentRequest(
            classified_intent=classified_intent,
            client_request=client_request,
        )

        # Test the handler runs without error
        with patch.object(self.skill, "send_response"):
            await self.skill._handle_system_help(intent_request)
            # Verify handler completed successfully (response sent in background task)

    async def test_process_request_routing(self):
        """Test that process_request routes to correct handlers."""
        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="set temperature to 22 degrees",
            room="livingroom",
            output_topic="test/output",
        )

        # Test DEVICE_SET routing
        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.95,
            entities={"numbers": [self._create_mock_entity("number", "22", 22)]},
            raw_text="set temperature to 22 degrees",
        )

        intent_request = IntentRequest(
            classified_intent=classified_intent,
            client_request=client_request,
        )

        with patch.object(self.skill, "_handle_device_set") as mock_handle_set:
            await self.skill.process_request(intent_request)
            mock_handle_set.assert_called_once_with(intent_request)

        # Test SYSTEM_HELP routing
        classified_intent = ClassifiedIntent(
            intent_type=IntentType.SYSTEM_HELP,
            confidence=0.85,
            entities={},
            raw_text="help with climate",
        )

        intent_request = IntentRequest(
            classified_intent=classified_intent,
            client_request=client_request,
        )

        with patch.object(self.skill, "_handle_system_help") as mock_handle_help:
            await self.skill.process_request(intent_request)
            mock_handle_help.assert_called_once_with(intent_request)

    async def test_process_request_unsupported_intent(self):
        """Test handling of unsupported intent types."""
        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="turn on the light",
            room="livingroom",
            output_topic="test/output",
        )

        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_ON,  # Unsupported for climate skill
            confidence=0.90,
            entities={},
            raw_text="turn on the light",
        )

        intent_request = IntentRequest(
            classified_intent=classified_intent,
            client_request=client_request,
        )

        with patch.object(self.skill, "send_response") as mock_send_response:
            await self.skill.process_request(intent_request)

            # Verify error response sent
            mock_send_response.assert_called_once()
            args = mock_send_response.call_args[0]
            self.assertIn("not sure", args[0].lower())

    async def test_render_response(self):
        """Test rendering response with templates."""
        parameters = Parameters()
        parameters.temperature = 22
        parameters.rooms = ["livingroom"]

        response = self.skill._render_response(IntentType.DEVICE_SET, parameters)

        self.assertEqual(response, "Temperature set")
        self.mock_set_template.render.assert_called_once()
