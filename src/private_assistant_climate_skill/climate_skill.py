import os
from enum import Enum

import homeassistant_api as ha_api
import jinja2
import paho.mqtt.client as mqtt
import private_assistant_commons as commons
from private_assistant_commons import messages
from private_assistant_commons.skill_logger import SkillLogger
from pydantic import BaseModel

from private_assistant_climate_skill import config

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logger = SkillLogger.get_logger(__name__)


class Parameters(BaseModel):
    temperature: int = 0
    targets: list[str] = []


class Action(Enum):
    HELP = "help"
    SET = "set"

    @classmethod
    def find_matching_action(cls, verbs: list):
        for action in cls:
            if action.value in verbs:
                return action
        return None


class ClimateSkill(commons.BaseSkill):
    def __init__(
        self,
        config_obj: config.SkillConfig,
        mqtt_client: mqtt.Client,
        ha_api_client: ha_api.Client,
        template_env: jinja2.Environment,
    ) -> None:
        super().__init__(config_obj, mqtt_client)
        self.ha_api_client: ha_api.Client = ha_api_client
        self.template_env: jinja2.Environment = template_env
        self.action_to_answer: dict[Action, jinja2.Template] = {}

        # Preload templates
        try:
            self.action_to_answer[Action.HELP] = self.template_env.get_template("help.j2")
            self.action_to_answer[Action.SET] = self.template_env.get_template("set_temperature.j2")
            logger.debug("Templates successfully loaded during initialization.")
        except jinja2.TemplateNotFound as e:
            logger.error(f"Failed to load template: {e}")

        self._target_cache: dict[str, ha_api.State] = {}
        self._target_alias_cache: dict[str, str] = {}

    @property
    def target_cache(self) -> dict[str, ha_api.State]:
        if len(self._target_cache) < 1:
            logger.debug("Fetching targets from Home Assistant API.")
            self._target_cache = self.get_targets()
        return self._target_cache

    @property
    def target_alias_cache(self) -> dict[str, str]:
        if len(self._target_alias_cache) < 1:
            logger.debug("Building target alias cache.")
            for target in self.target_cache.values():
                alias = target.attributes.get("friendly_name", "no name").lower()
                self._target_alias_cache[target.entity_id] = alias
        return self._target_alias_cache

    def calculate_certainty(self, intent_analysis_result: messages.IntentAnalysisResult) -> float:
        if "temperature" in intent_analysis_result.nouns:
            logger.debug("Temperature noun detected, certainty set to 1.0.")
            return 1.0
        logger.debug("No temperature noun detected, certainty set to 0.")
        return 0

    def get_targets(self) -> dict[str, ha_api.State]:
        entity_groups = self.ha_api_client.get_entities()
        room_entities = {entity_name: entity.state for entity_name, entity in entity_groups["climate"].entities.items()}
        logger.debug(f"Retrieved {len(room_entities)} climate entities from Home Assistant.")
        return room_entities

    def find_parameter_targets(self, room: str) -> list[str]:
        room = room.lower()
        targets = [target for target in self.target_alias_cache.keys() if room in target]
        logger.debug(f"Found {len(targets)} targets matching room '{room}'.")
        return targets

    def find_parameters(self, action: Action, intent_analysis_result: messages.IntentAnalysisResult) -> Parameters:
        parameters = Parameters()
        if action == Action.SET:
            parameters.targets = self.find_parameter_targets(room=intent_analysis_result.client_request.room)
            if len(intent_analysis_result.numbers) > 0:
                parameters.temperature = intent_analysis_result.numbers[0].number_token
            logger.debug(f"Parameters found for action SET: {parameters}.")
        return parameters

    def get_answer(self, action: Action, parameters: Parameters) -> str:
        template = self.action_to_answer.get(action)
        if template:
            answer = template.render(
                action=action,
                parameters=parameters,
                target_alias_cache=self.target_alias_cache,
            )
            logger.debug(f"Generated answer using template for action {action}.")
            return answer
        else:
            logger.error(f"No template found for action {action}.")
            return "Sorry, I couldn't process your request."

    def call_action_api(self, action: Action, parameters: Parameters) -> None:
        service = self.ha_api_client.get_domain("climate")
        if service is None:
            logger.error("Failed to retrieve the climate service from Home Assistant API.")
        else:
            for target in parameters.targets:
                if action == Action.SET:
                    logger.debug(f"Setting temperature to {parameters.temperature} for target '{target}'.")
                    service.set_temperature(entity_id=target, temperature=parameters.temperature)

    def process_request(self, intent_analysis_result: messages.IntentAnalysisResult) -> None:
        action = Action.find_matching_action(intent_analysis_result.verbs)
        if action is None:
            logger.error(f"Unrecognized action in verbs: {intent_analysis_result.verbs}")
            return

        parameters = self.find_parameters(action, intent_analysis_result=intent_analysis_result)
        if parameters.targets:
            answer = self.get_answer(action, parameters)
            self.add_text_to_output_topic(answer, client_request=intent_analysis_result.client_request)
            if action not in [Action.HELP]:
                self.call_action_api(action, parameters)
        else:
            logger.error("No targets found for action.")
