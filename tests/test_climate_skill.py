import unittest
from unittest.mock import Mock, patch

import jinja2
import sqlmodel
from private_assistant_commons import messages

from private_assistant_climate_skill import models
from private_assistant_climate_skill.climate_skill import Action, ClimateSkill, Parameters


class TestClimateSkill(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Set up an in-memory SQLite database
        cls.engine = sqlmodel.create_engine("sqlite:///:memory:", echo=False)
        sqlmodel.SQLModel.metadata.create_all(cls.engine)

    def setUp(self):
        # Create a new session for each test
        self.session = sqlmodel.Session(self.engine)

        # Mock the MQTT client and other dependencies
        self.mock_mqtt_client = Mock()
        self.mock_config = Mock()
        self.mock_template_env = Mock(spec=jinja2.Environment)

        # Create an instance of ClimateSkill using the in-memory DB and mocked dependencies
        self.skill = ClimateSkill(
            config_obj=self.mock_config,
            mqtt_client=self.mock_mqtt_client,
            db_engine=self.engine,
            template_env=self.mock_template_env,
        )

    def tearDown(self):
        # Clean up the session after each test
        self.session.close()

    def test_get_devices(self):
        # Insert a mock device into the in-memory SQLite database
        mock_device = models.ClimateSkillDevice(
            id=1,
            topic="livingroom/climate/main",
            alias="main thermostat",
            room="livingroom",
            payload_set_template='{"occupied_heating_setpoint": {{ temperature }}}',
        )
        with self.session as session:
            session.add(mock_device)
            session.commit()

        # Fetch devices for the "livingroom"
        devices = self.skill.get_devices("livingroom")

        # Assert that the correct device is returned
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].alias, "main thermostat")
        self.assertEqual(devices[0].topic, "livingroom/climate/main")

    def test_find_parameters(self):
        # Insert a mock device into the in-memory SQLite database
        mock_device = models.ClimateSkillDevice(
            topic="livingroom/climate/main",
            alias="main thermostat",
            room="livingroom",
            payload_set_template='{"occupied_heating_setpoint": {{ temperature }}}',
        )

        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)

        # Create a mock for the `client_request` attribute
        mock_client_request = Mock()
        mock_client_request.room = "livingroom"
        mock_intent_result.client_request = mock_client_request
        mock_intent_result.nouns = ["temperature"]
        mock_intent_result.numbers = [Mock(number_token=22)]  # Setting temperature to 22°C

        with patch.object(self.skill, "get_devices", return_value=[mock_device]):
            # Find parameters for setting the temperature
            parameters = self.skill.find_parameters(Action.SET, mock_intent_result)

        # Assert that the correct device and temperature are in the parameters
        self.assertEqual(len(parameters.targets), 1)
        self.assertEqual(parameters.targets[0].alias, "main thermostat")
        self.assertEqual(parameters.temperature, 22)

    def test_calculate_certainty_with_temperature(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.nouns = ["temperature"]
        certainty = self.skill.calculate_certainty(mock_intent_result)
        self.assertEqual(certainty, 1.0)

    def test_calculate_certainty_without_temperature(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.nouns = ["humidity"]  # No "temperature"
        certainty = self.skill.calculate_certainty(mock_intent_result)
        self.assertEqual(certainty, 0)

    @patch("private_assistant_climate_skill.climate_skill.logger")
    def test_send_mqtt_command(self, mock_logger):
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

        # Call the method to send the MQTT command (for setting temperature)
        self.skill.send_mqtt_command(Action.SET, parameters)

        # Assert that the MQTT client sent the correct payload to the correct topic
        self.mock_mqtt_client.publish.assert_called_once_with(
            "livingroom/climate/main", '{"occupied_heating_setpoint": 22}', qos=1
        )
        mock_logger.info.assert_called_with(
            "Sending payload %s to topic %s via MQTT.", '{"occupied_heating_setpoint": 22}', "livingroom/climate/main"
        )

    def test_process_request_with_set_action(self):
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
            patch.object(self.skill, "add_text_to_output_topic") as mock_add_text_to_output_topic,
        ):
            # Execute the process_request method
            self.skill.process_request(mock_intent_result)

            # Assert that methods were called with expected arguments
            mock_get_answer.assert_called_once_with(Action.SET, mock_parameters)
            mock_send_mqtt_command.assert_called_once_with(Action.SET, mock_parameters)
            mock_add_text_to_output_topic.assert_called_once_with(
                "Setting temperature to 22°C", client_request=mock_intent_result.client_request
            )
