import logging
from enum import Enum

import homeassistant_api as ha_api
import jinja2
import paho.mqtt.client as mqtt
import private_assistant_commons as commons
from private_assistant_commons import messages
from pydantic import BaseModel

from private_assistant_climate_skill import config

logger = logging.getLogger(__name__)


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
        self.action_to_answer: dict[Action, str] = {
            Action.HELP: "help.j2",
            Action.SET: "set_temperature.j2",
        }
        self._target_cache: dict[str, ha_api.State] = {}
        self._target_alias_cache: dict[str, str] = {}

    @property
    def target_cache(self) -> dict[str, ha_api.State]:
        if len(self._target_cache) < 1:
            self._target_cache = self.get_targets()
        return self._target_cache

    @property
    def target_alias_cache(self) -> dict[str, str]:
        if len(self._target_alias_cache) < 1:
            for target in self.target_cache.values():
                alias = target.attributes.get("friendly_name", "no name").lower()
                self._target_alias_cache[target.entity_id] = alias
        return self._target_alias_cache

    def calculate_certainty(self, intent_analysis_result: messages.IntentAnalysisResult) -> float:
        if ["temperature"] in intent_analysis_result.nouns:
            return 1.0
        return 0

    def get_targets(self) -> dict[str, ha_api.State]:
        entity_groups = self.ha_api_client.get_entities()
        room_entities = {entity_name: entity.state for entity_name, entity in entity_groups["climate"].entities.items()}
        return room_entities

    def find_parameter_targets(self, room: str) -> list[str]:
        return [target for target in self.target_alias_cache.keys() if room in target]

    def find_parameters(self, action: Action, intent_analysis_result: messages.IntentAnalysisResult) -> Parameters:
        parameters = Parameters()
        if action == Action.SET:
            parameters.targets = self.find_parameter_targets(room=intent_analysis_result.client_request.room)
            if len(intent_analysis_result.numbers) > 0:
                parameters.temperature = intent_analysis_result.numbers[0].number_token
        return parameters

    def get_answer(self, action: Action, parameters: Parameters) -> str:
        template = self.template_env.get_template(self.action_to_answer[action])
        answer = template.render(
            action=action,
            parameters=parameters,
            target_alias_cache=self.target_alias_cache,
        )
        return answer

    def call_action_api(self, action: Action, parameters: Parameters) -> None:
        service = self.ha_api_client.get_domain("climate")
        if service is None:
            logger.error("Service is None.")
        else:
            for target in parameters.targets:
                if action == Action.SET:
                    service.set_temperature(entity_id=target, temperature=parameters.temperature)

    def process_request(self, intent_analysis_result: messages.IntentAnalysisResult) -> None:
        action = Action.find_matching_action(intent_analysis_result.verbs)
        parameters = None
        if action is not None:
            parameters = self.find_parameters(action, intent_analysis_result=intent_analysis_result)
        if parameters is not None and action is not None:
            answer = self.get_answer(action, parameters)
            self.add_text_to_output_topic(answer, client_request=intent_analysis_result.client_request)
            if action not in [Action.HELP]:
                self.call_action_api(action, parameters)
