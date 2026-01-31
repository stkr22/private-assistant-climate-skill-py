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
        # Mock global_devices with realistic MQTT topics and payload templates
        mock_device_1 = self._create_mock_global_device(
            "main",
            "study",
            "zigbee2mqtt/study/thermostat/main/set",
            '{"occupied_heating_setpoint": {{ temperature }}}',
        )
        mock_device_2 = self._create_mock_global_device(
            "main",
            "office",
            "zigbee2mqtt/office/thermostat/main/set",
            '{"occupied_heating_setpoint": {{ temperature }}}',
        )
        mock_device_3 = self._create_mock_global_device(
            "main",
            "workshop",
            "zigbee2mqtt/workshop/thermostat/main/set",
            '{"occupied_heating_setpoint": {{ temperature }}}',
        )

        self.skill.global_devices = [mock_device_1, mock_device_2, mock_device_3]

        # Fetch devices for "study"
        devices = await self.skill.get_devices(["study"])

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].alias, "main")
        self.assertEqual(devices[0].topic, "zigbee2mqtt/study/thermostat/main/set")
        self.assertEqual(devices[0].room, "study")

        # Fetch devices for "study" and "office"
        devices = await self.skill.get_devices(["study", "office"])

        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0].alias, "main")
        self.assertEqual(devices[1].alias, "main")

    async def test_find_parameters_with_temperature(self):
        """Test extracting parameters from classified intent with temperature."""
        # Mock global_devices with realistic MQTT topic
        mock_device = self._create_mock_global_device(
            "main",
            "study",
            "zigbee2mqtt/study/thermostat/main/set",
            '{"occupied_heating_setpoint": {{ temperature }}}',
        )
        self.skill.global_devices = [mock_device]

        # Create mock classified intent
        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.95,
            entities={
                "number": [self._create_mock_entity("number", "21", 21)],
                "room": [self._create_mock_entity("room", "study", "study")],
            },
            raw_text="set temperature to 21 degrees in study",
        )

        # Find parameters
        parameters = await self.skill.find_parameters(IntentType.DEVICE_SET, classified_intent, "study")

        self.assertEqual(len(parameters.targets), 1)
        self.assertEqual(parameters.targets[0].alias, "main")
        self.assertEqual(parameters.temperature, 21)
        self.assertEqual(parameters.rooms, ["study"])

    async def test_find_parameters_fallback_to_current_room(self):
        """Test that parameters fallback to current room when no room entities."""
        # Mock global_devices with realistic MQTT topic
        mock_device = self._create_mock_global_device(
            "main",
            "workshop",
            "zigbee2mqtt/workshop/thermostat/main/set",
            '{"occupied_heating_setpoint": {{ temperature }}}',
        )
        self.skill.global_devices = [mock_device]

        # Create mock classified intent without room entities
        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.95,
            entities={
                "number": [self._create_mock_entity("number", "19", 19)],
            },
            raw_text="set temperature to 19 degrees",
        )

        # Find parameters (should use current_room)
        parameters = await self.skill.find_parameters(IntentType.DEVICE_SET, classified_intent, "workshop")

        self.assertEqual(len(parameters.targets), 1)
        self.assertEqual(parameters.targets[0].alias, "main")
        self.assertEqual(parameters.temperature, 19)
        self.assertEqual(parameters.rooms, ["workshop"])

    async def test_handle_device_set(self):
        """Test handling DEVICE_SET intent."""
        # Mock global_devices with realistic MQTT topic
        mock_device = self._create_mock_global_device(
            "main",
            "office",
            "zigbee2mqtt/office/thermostat/main/set",
            '{"occupied_heating_setpoint": {{ temperature }}}',
        )
        self.skill.global_devices = [mock_device]

        # Create mock intent request
        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="set temperature to 23 degrees",
            room="office",
            output_topic="assistant/office/output",
        )

        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.95,
            entities={
                "number": [self._create_mock_entity("number", "23", 23)],
            },
            raw_text="set temperature to 23 degrees",
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
            text="set temperature to 20 degrees",
            room="garage",
            output_topic="assistant/garage/output",
        )

        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.95,
            entities={
                "number": [self._create_mock_entity("number", "20", 20)],
            },
            raw_text="set temperature to 20 degrees",
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

    async def test_process_request_routing(self):
        """Test that process_request routes to correct handlers."""
        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="set temperature to 24 degrees",
            room="office",
            output_topic="assistant/office/output",
        )

        # Test DEVICE_SET routing
        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.95,
            entities={
                "number": [
                    self._create_mock_entity("number", "24", 24).model_copy(update={"metadata": {"unit": "celsius"}})
                ]
            },
            raw_text="set temperature to 24 degrees",
        )

        intent_request = IntentRequest(
            classified_intent=classified_intent,
            client_request=client_request,
        )

        with patch.object(self.skill, "_handle_device_set") as mock_handle_set:
            await self.skill.process_request(intent_request)
            mock_handle_set.assert_called_once_with(intent_request)

    async def test_process_request_unsupported_intent(self):
        """Test handling of unsupported intent types."""
        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="turn on the light",
            room="workshop",
            output_topic="assistant/workshop/output",
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

    async def test_is_climate_intent_with_thermostat_device(self):
        """Test that thermostat device entities are recognized as climate intents."""
        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.9,
            entities={
                "device": [
                    self._create_mock_entity("device", "thermostat", "thermostat").model_copy(
                        update={"metadata": {"device_type": "thermostat", "is_generic": False}}
                    )
                ]
            },
            raw_text="set thermostat to 20 degrees",
        )

        self.assertTrue(self.skill._is_climate_intent(classified_intent))

    async def test_is_climate_intent_with_generic_thermostat(self):
        """Test that generic thermostat references are recognized as climate intents."""
        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.9,
            entities={
                "device": [
                    self._create_mock_entity("device", "thermostat", "thermostat").model_copy(
                        update={"metadata": {"device_type": "thermostat", "is_generic": True}}
                    )
                ]
            },
            raw_text="set thermostat to 21 degrees",
        )

        self.assertTrue(self.skill._is_climate_intent(classified_intent))

    async def test_is_climate_intent_with_celsius_number(self):
        """Test that number entities with celsius unit are recognized as climate intents."""
        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.9,
            entities={
                "number": [
                    self._create_mock_entity("number", "22", 22).model_copy(
                        update={"metadata": {"unit": "celsius", "next_token": "degrees"}}
                    )
                ]
            },
            raw_text="set temperature to 22 degrees",
        )

        self.assertTrue(self.skill._is_climate_intent(classified_intent))

    async def test_is_climate_intent_rejects_curtain_device(self):
        """Test that curtain device entities are NOT recognized as climate intents."""
        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.9,
            entities={
                "device": [
                    self._create_mock_entity("device", "curtains", "curtains").model_copy(
                        update={"metadata": {"device_type": "curtain", "is_generic": False}}
                    )
                ],
                "number": [
                    self._create_mock_entity("number", "77", 77).model_copy(
                        update={"metadata": {"unit": "brightness", "next_token": "."}}
                    )
                ],
            },
            raw_text="set curtains to level 77",
        )

        self.assertFalse(self.skill._is_climate_intent(classified_intent))

    async def test_is_climate_intent_rejects_brightness_number(self):
        """Test that number entities with brightness unit are NOT recognized as climate intents."""
        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.9,
            entities={
                "number": [
                    self._create_mock_entity("number", "50", 50).model_copy(
                        update={"metadata": {"unit": "brightness", "next_token": "percent"}}
                    )
                ]
            },
            raw_text="set brightness to 50 percent",
        )

        self.assertFalse(self.skill._is_climate_intent(classified_intent))

    async def test_process_request_ignores_non_climate_intent(self):
        """Test that non-climate DEVICE_SET intents are ignored without response."""
        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="set curtains to level 77",
            room="study",
            output_topic="assistant/study/output",
        )

        # Curtain device with brightness number (not climate-related)
        classified_intent = ClassifiedIntent(
            intent_type=IntentType.DEVICE_SET,
            confidence=0.9,
            entities={
                "device": [
                    self._create_mock_entity("device", "curtains", "curtains").model_copy(
                        update={"metadata": {"device_type": "curtain", "is_generic": False}}
                    )
                ],
                "number": [
                    self._create_mock_entity("number", "77", 77).model_copy(update={"metadata": {"unit": "brightness"}})
                ],
            },
            raw_text="set curtains to level 77",
        )

        intent_request = IntentRequest(
            classified_intent=classified_intent,
            client_request=client_request,
        )

        # Mock send_response to verify it's NOT called
        with patch.object(self.skill, "send_response") as mock_send_response:
            await self.skill.process_request(intent_request)

            # Verify NO response was sent
            mock_send_response.assert_not_called()
