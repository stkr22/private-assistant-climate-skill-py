from enum import Enum

import jinja2
import paho.mqtt.client as mqtt
import private_assistant_commons as commons
import sqlalchemy
from private_assistant_commons import messages
from private_assistant_commons.skill_logger import SkillLogger
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, select

from private_assistant_climate_skill.models import ClimateSkillDevice

logger = SkillLogger.get_logger(__name__)


class Parameters(BaseModel):
    temperature: int = 0
    targets: list[ClimateSkillDevice] = []  # Targets are devices from the database


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
        config_obj: commons.SkillConfig,
        mqtt_client: mqtt.Client,
        db_engine: sqlalchemy.Engine,
        template_env: jinja2.Environment,
    ) -> None:
        super().__init__(config_obj, mqtt_client)
        self.db_engine = db_engine
        self.template_env = template_env
        self._device_cache: dict[str, list[ClimateSkillDevice]] = {}  # Cache devices by room
        self.action_to_answer: dict[Action, jinja2.Template] = {}

        # Preload templates
        try:
            self.action_to_answer[Action.HELP] = self.template_env.get_template("help.j2")
            self.action_to_answer[Action.SET] = self.template_env.get_template("set_temperature.j2")
            logger.debug("Templates successfully loaded during initialization.")
        except jinja2.TemplateNotFound as e:
            logger.error("Failed to load template: %s", e)

    @property
    def device_cache(self) -> dict[str, list[ClimateSkillDevice]]:
        """Lazy-loaded cache for devices."""
        if not self._device_cache:
            logger.debug("Loading devices into cache.")
            with Session(self.db_engine) as session:
                statement = select(ClimateSkillDevice)
                devices = session.exec(statement).all()
                for device in devices:
                    try:
                        device.model_validate(device)
                        if device.room not in self._device_cache:
                            self._device_cache[device.room] = []
                        self._device_cache[device.room].append(device)
                    except ValidationError as e:
                        logger.error("Validation error loading device into cache: %s", e)
        return self._device_cache

    def get_devices(self, room: str) -> list[ClimateSkillDevice]:
        """Return devices for a specific room, using cache."""
        logger.info("Fetching devices for room: %s", room)
        return self.device_cache.get(room, [])

    def calculate_certainty(self, intent_analysis_result: messages.IntentAnalysisResult) -> float:
        if "temperature" in intent_analysis_result.nouns:
            logger.debug("Temperature noun detected, certainty set to 1.0.")
            return 1.0
        logger.debug("No temperature noun detected, certainty set to 0.")
        return 0

    def find_parameters(self, action: Action, intent_analysis_result: messages.IntentAnalysisResult) -> Parameters:
        parameters = Parameters()
        devices = self.get_devices(intent_analysis_result.client_request.room)
        if action == Action.SET:
            parameters.targets = [device for device in devices]
            if intent_analysis_result.numbers:
                parameters.temperature = intent_analysis_result.numbers[0].number_token
        logger.debug("Parameters found for action %s: %s", action, parameters)
        return parameters

    def get_answer(self, action: Action, parameters: Parameters) -> str:
        template = self.action_to_answer.get(action)
        if template:
            answer = template.render(
                action=action,
                parameters=parameters,
            )
            logger.debug("Generated answer using template for action %s.", action)
            return answer
        else:
            logger.error("No template found for action %s.", action)
            return "Sorry, I couldn't process your request."

    def send_mqtt_command(self, action: Action, parameters: Parameters) -> None:
        for device in parameters.targets:
            if action == Action.SET:
                payload = jinja2.Template(device.payload_set_template).render(temperature=parameters.temperature)
            else:
                logger.error("Unknown action: %s", action)
                continue

            logger.info("Sending payload %s to topic %s via MQTT.", payload, device.topic)
            try:
                self.mqtt_client.publish(device.topic, payload, qos=1)
            except Exception as e:
                logger.error("Failed to send MQTT message to topic %s: %s", device.topic, e, exc_info=True)

    def process_request(self, intent_analysis_result: messages.IntentAnalysisResult) -> None:
        action = Action.find_matching_action(intent_analysis_result.verbs)
        if action is None:
            logger.error("Unrecognized action in verbs: %s", intent_analysis_result.verbs)
            return

        parameters = self.find_parameters(action, intent_analysis_result)
        if parameters.targets:
            answer = self.get_answer(action, parameters)
            self.add_text_to_output_topic(answer, client_request=intent_analysis_result.client_request)
            if action not in [Action.HELP]:
                self.send_mqtt_command(action, parameters)
        else:
            logger.error("No targets found for action %s.", action)
