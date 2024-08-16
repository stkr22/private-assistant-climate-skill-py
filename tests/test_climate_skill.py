import unittest
from unittest.mock import Mock, patch

import homeassistant_api as ha_api
import jinja2
from homeassistant_api import Entity, Group, State
from private_assistant_climate_skill.climate_skill import Action, ClimateSkill, Parameters
from private_assistant_commons import messages


class TestClimateSkill(unittest.TestCase):
    def setUp(self):
        self.mock_mqtt_client = Mock()
        self.mock_config = Mock()
        self.mock_ha_api_client = Mock(spec=ha_api.Client)
        self.mock_template_env = Mock(spec=jinja2.Environment)

        self.skill = ClimateSkill(
            config_obj=self.mock_config,
            mqtt_client=self.mock_mqtt_client,
            ha_api_client=self.mock_ha_api_client,
            template_env=self.mock_template_env,
        )

    def test_calculate_certainty_with_temperature(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.nouns = ["temperature"]
        certainty = self.skill.calculate_certainty(mock_intent_result)
        self.assertEqual(certainty, 1.0)

    def test_calculate_certainty_without_temperature(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.nouns = ["humidity"]
        certainty = self.skill.calculate_certainty(mock_intent_result)
        self.assertEqual(certainty, 0)

    def test_get_targets(self):
        mock_entity_groups = {"climate": Mock()}
        mock_entity_groups["climate"].entities = {"entity_id_1": Mock(state="cool")}
        self.mock_ha_api_client.get_entities.return_value = mock_entity_groups

        targets = self.skill.get_targets()
        self.assertIn("entity_id_1", targets)
        self.assertEqual(targets["entity_id_1"], "cool")

    def test_find_parameter_targets(self):
        self.skill._target_alias_cache = {
            "kitchen/thermostat/main": "Thermostat",
            "livingroom/thermostat/main": "Thermostat",
            "bedroom/thermostat/main": "Thermostat",
            "kitchen/thermostat/backup": "Thermostat",
        }

        # "kitchen" should match both kitchen-related entity_ids
        targets = self.skill.find_parameter_targets("kitchen")
        self.assertEqual(targets, ["kitchen/thermostat/main", "kitchen/thermostat/backup"])

        # "livingroom" should match the livingroom-related entity_id
        targets = self.skill.find_parameter_targets("livingroom")
        self.assertEqual(targets, ["livingroom/thermostat/main"])

        # "bed" should match the bedroom-related entity_id
        targets = self.skill.find_parameter_targets("bed")
        self.assertEqual(targets, ["bedroom/thermostat/main"])

    def test_get_answer(self):
        # Set up mock template and return value
        mock_template = Mock()
        mock_template.render.return_value = "Set temperature to 22"

        # Ensure action_to_answer is a dictionary with Action keys and Mock templates as values
        self.skill.action_to_answer = {Action.SET: mock_template, Action.HELP: mock_template}

        # Mock the State object
        mock_state = Mock(spec=State)
        mock_state.entity_id = "kitchen/thermostat/main"
        mock_state.state = "cool"
        mock_state.attributes = {"friendly_name": "Kitchen Thermostat"}

        # Mock the Entity object that contains the State
        mock_entity = Mock(spec=Entity)
        mock_entity.slug = "thermostat/main"
        mock_entity.state = mock_state
        mock_entity.entity_id = "kitchen/thermostat/main"

        # Mock the Group object that contains the Entity
        mock_group = Mock(spec=Group)
        mock_group.group_id = "climate"
        mock_group.entities = {"entity_id_1": mock_entity}

        # Mock the ha_api_client to return the Group
        self.skill.ha_api_client.get_entities.return_value = {"climate": mock_group}

        # Force the cache to be built by accessing it
        _ = self.skill.target_alias_cache  # This builds the alias cache using the mocked data

        # Define the parameters and action
        mock_parameters = Parameters(temperature=22, targets=["entity_id_1"])
        mock_action = Action.SET

        # Call the method and check the result
        answer = self.skill.get_answer(mock_action, mock_parameters)
        self.assertEqual(answer, "Set temperature to 22")
        mock_template.render.assert_called_once_with(
            action=mock_action, parameters=mock_parameters, target_alias_cache=self.skill.target_alias_cache
        )

    @patch("private_assistant_climate_skill.climate_skill.logger")
    def test_call_action_api(self, mock_logger):
        mock_service = Mock()
        self.mock_ha_api_client.get_domain.return_value = mock_service

        parameters = Parameters(temperature=22, targets=["entity_id_1"])
        self.skill.call_action_api(Action.SET, parameters)

        mock_service.set_temperature.assert_called_once_with(entity_id="entity_id_1", temperature=22)
        mock_logger.error.assert_not_called()

    def test_process_request_with_valid_action(self):
        # Mocking the client request
        mock_client_request = Mock()
        mock_client_request.room = "living room"

        # Mocking the IntentAnalysisResult
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.verbs = ["set"]
        mock_intent_result.numbers = [Mock(number_token=22)]
        mock_intent_result.client_request = mock_client_request

        # Mock the parameters expected to be used in the processing
        mock_parameters = Parameters(temperature=22, targets=["entity_id_1"])

        # Set up the mock return values and behaviors
        with (
            patch.object(self.skill, "get_answer", return_value="Set temperature to 22") as mock_get_answer,
            patch.object(self.skill, "call_action_api") as mock_call_action_api,
            patch.object(self.skill, "find_parameter_targets", return_value=["entity_id_1"]),
            patch.object(self.skill, "add_text_to_output_topic") as mock_add_text_to_output_topic,
        ):
            # Call the method under test
            self.skill.process_request(mock_intent_result)

            # Assertions
            mock_get_answer.assert_called_once_with(Action.SET, mock_parameters)
            mock_call_action_api.assert_called_once_with(Action.SET, mock_parameters)
            mock_add_text_to_output_topic.assert_called_once_with(
                "Set temperature to 22", client_request=mock_intent_result.client_request
            )

    def test_process_request_with_invalid_action(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.verbs = ["unknown_action"]

        with (
            patch.object(self.skill, "get_answer", return_value="Action not found") as mock_get_answer,
            patch.object(self.skill, "add_text_to_output_topic") as mock_add_text_to_output_topic,
        ):
            self.skill.process_request(mock_intent_result)

            mock_get_answer.assert_not_called()
            mock_add_text_to_output_topic.assert_not_called()
