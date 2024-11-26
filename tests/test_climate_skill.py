import logging
import unittest
from unittest.mock import AsyncMock, Mock, patch

import jinja2
from private_assistant_commons import messages
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from private_assistant_climate_skill import models
from private_assistant_climate_skill.climate_skill import Action, ClimateSkill, Parameters


class TestClimateSkill(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Set up an in-memory SQLite database for async usage
        cls.engine_async = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def asyncSetUp(self):
        # Create tables asynchronously before each test
        async with self.engine_async.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        # Create mock components for testing
        self.mock_mqtt_client = AsyncMock()
        self.mock_config = Mock()
        self.mock_template_env = Mock(spec=jinja2.Environment)
        self.mock_task_group = AsyncMock()
        self.mock_logger = Mock(logging.Logger)
        async with self.engine_async.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        # Create an instance of ClimateSkill using the in-memory DB and mocked dependencies
        self.skill = ClimateSkill(
            config_obj=self.mock_config,
            mqtt_client=self.mock_mqtt_client,
            db_engine=self.engine_async,
            template_env=self.mock_template_env,
            task_group=self.mock_task_group,
            logger=self.mock_logger,
        )

    async def asyncTearDown(self):
        # Drop tables asynchronously after each test to ensure a clean state
        async with self.engine_async.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)

    async def test_get_devices(self):
        # Insert mock devices into the in-memory SQLite database
        mock_device_1 = models.ClimateSkillDevice(
            id=1,
            topic="livingroom/climate/main",
            alias="main thermostat",
            room="livingroom",
            payload_set_template='{"occupied_heating_setpoint": {{ temperature }}}',
        )
        mock_device_2 = models.ClimateSkillDevice(
            id=2,
            topic="bedroom/climate/main",
            alias="bedroom thermostat",
            room="bedroom",
            payload_set_template='{"occupied_heating_setpoint": {{ temperature }}}',
        )
        mock_device_3 = models.ClimateSkillDevice(
            topic="kitchen/climate/main",
            alias="kitchen thermostat",
            room="kitchen",
            payload_set_template='{"occupied_heating_setpoint": {{ temperature }}}',
        )
        async with AsyncSession(self.engine_async) as session:
            async with session.begin():
                session.add_all([mock_device_1, mock_device_2, mock_device_3])

        # Fetch devices for "livingroom"
        devices = await self.skill.get_devices(["livingroom"])

        # Assert that the correct device is returned
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].alias, "main thermostat")
        self.assertEqual(devices[0].topic, "livingroom/climate/main")

        # Fetch devices for "livingroom" and "bedroom"
        devices = await self.skill.get_devices(["livingroom", "bedroom"])

        # Assert that the correct devices are returned
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0].alias, "main thermostat")
        self.assertEqual(devices[0].topic, "livingroom/climate/main")
        self.assertEqual(devices[1].alias, "bedroom thermostat")
        self.assertEqual(devices[1].topic, "bedroom/climate/main")

    async def test_find_parameters(self):
        # Insert mock devices into the in-memory SQLite database
        mock_device_1 = models.ClimateSkillDevice(
            topic="livingroom/climate/main",
            alias="main thermostat",
            room="livingroom",
            payload_set_template='{"occupied_heating_setpoint": {{ temperature }}}',
        )
        mock_device_2 = models.ClimateSkillDevice(
            topic="bedroom/climate/main",
            alias="bedroom thermostat",
            room="bedroom",
            payload_set_template='{"occupied_heating_setpoint": {{ temperature }}}',
        )
        mock_device_3 = models.ClimateSkillDevice(
            topic="kitchen/climate/main",
            alias="kitchen thermostat",
            room="kitchen",
            payload_set_template='{"occupied_heating_setpoint": {{ temperature }}}',
        )

        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_client_request = Mock()
        mock_client_request.room = "kitchen"
        mock_intent_result.client_request = mock_client_request
        mock_intent_result.rooms = ["livingroom", "bedroom"]  # Updated to reflect multiple rooms
        mock_intent_result.nouns = ["temperature"]
        mock_intent_result.numbers = [Mock(number_token=22)]  # Setting temperature to 22°C

        with patch.object(self.skill, "get_devices", return_value=[mock_device_1, mock_device_2]):
            # Find parameters for setting the temperature
            parameters = await self.skill.find_parameters(Action.SET, mock_intent_result)

        # Assert that the correct devices and temperature are in the parameters
        self.assertEqual(len(parameters.targets), 2)
        self.assertEqual(parameters.targets[0].alias, "main thermostat")
        self.assertEqual(parameters.targets[1].alias, "bedroom thermostat")
        self.assertEqual(parameters.temperature, 22)

        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_client_request = Mock()
        mock_client_request.room = "kitchen"
        mock_intent_result.client_request = mock_client_request
        mock_intent_result.rooms = []
        mock_intent_result.nouns = ["temperature"]
        mock_intent_result.numbers = [Mock(number_token=22)]  # Setting temperature to 22°C

        with patch.object(self.skill, "get_devices", return_value=[mock_device_3]):
            # Find parameters for setting the temperature
            parameters = await self.skill.find_parameters(Action.SET, mock_intent_result)

        # Assert that the correct devices and temperature are in the parameters
        self.assertEqual(len(parameters.targets), 1)
        self.assertEqual(parameters.targets[0].alias, "kitchen thermostat")
        self.assertEqual(parameters.temperature, 22)

    async def test_calculate_certainty_with_temperature(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.nouns = ["temperature"]
        certainty = await self.skill.calculate_certainty(mock_intent_result)
        self.assertEqual(certainty, 1.0)

    async def test_calculate_certainty_without_temperature(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.nouns = ["humidity"]  # No "temperature"
        certainty = await self.skill.calculate_certainty(mock_intent_result)
        self.assertEqual(certainty, 0)

    async def test_send_mqtt_command(self):
        # Create mock device
        mock_device = models.ClimateSkillDevice(
            id=1,
            topic="livingroom/climate/main",
            alias="main thermostat",
            room="livingroom",
            payload_set_template='{"occupied_heating_setpoint": {{ temperature }}}',
        )

        # Mock parameters for setting the temperature
        parameters = Parameters(targets=[mock_device], temperature=22)

        # Call the async method to send the MQTT command
        await self.skill.send_mqtt_command(Action.SET, parameters)

        # Assert that the MQTT client sent the correct payload to the correct topic
        self.mock_mqtt_client.publish.assert_called_once_with(
            "livingroom/climate/main", '{"occupied_heating_setpoint": 22}', qos=1
        )
        self.mock_logger.info.assert_called_with(
            "Sending payload %s to topic %s via MQTT.", '{"occupied_heating_setpoint": 22}', "livingroom/climate/main"
        )

    async def test_process_request_with_set_action(self):
        mock_device = models.ClimateSkillDevice(
            id=1,
            topic="livingroom/climate/main",
            alias="main thermostat",
            room="livingroom",
            payload_set_template='{"occupied_heating_setpoint": {{ temperature }}}',
        )
        # Mock the client request
        mock_client_request = Mock()
        mock_client_request.room = "livingroom"
        mock_client_request.text = "set the temperature to 22 degrees"

        # Mock the IntentAnalysisResult with spec
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.client_request = mock_client_request
        mock_intent_result.verbs = ["set"]
        mock_intent_result.nouns = ["temperature"]
        mock_intent_result.numbers = [Mock(number_token=22)]  # Setting temperature to 22°C

        # Set up mock parameters and method patches
        mock_parameters = Parameters(targets=[mock_device], temperature=22)

        with (
            patch.object(self.skill, "get_answer", return_value="Setting temperature to 22°C") as mock_get_answer,
            patch.object(self.skill, "send_mqtt_command") as mock_send_mqtt_command,
            patch.object(self.skill, "find_parameters", return_value=mock_parameters),
            patch.object(self.skill, "send_response") as mock_send_response,
        ):
            # Execute the process_request method
            await self.skill.process_request(mock_intent_result)

            # Assert that methods were called with expected arguments
            mock_get_answer.assert_called_once_with(Action.SET, mock_parameters)
            mock_send_mqtt_command.assert_called_once_with(Action.SET, mock_parameters)
            mock_send_response.assert_called_once_with(
                "Setting temperature to 22°C", client_request=mock_intent_result.client_request
            )
