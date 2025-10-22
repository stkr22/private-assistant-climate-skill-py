import asyncio
import logging

import aiomqtt
import jinja2
import private_assistant_commons as commons
from private_assistant_commons import (
    ClassifiedIntent,
    IntentRequest,
    IntentType,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from private_assistant_climate_skill.models import ClimateSkillDevice


class Parameters(BaseModel):
    temperature: int = 0
    targets: list[ClimateSkillDevice] = []
    rooms: list[str] = []


class ClimateSkill(commons.BaseSkill):
    """Climate control skill for managing HVAC devices via MQTT.

    Processes voice commands to control temperature settings for climate devices.
    Integrates with global device registry for device discovery and management.
    """

    def __init__(  # noqa: PLR0913
        self,
        config_obj: commons.SkillConfig,
        mqtt_client: aiomqtt.Client,
        db_engine: AsyncEngine,
        template_env: jinja2.Environment,
        task_group: asyncio.TaskGroup,
        logger: logging.Logger,
    ) -> None:
        """Initialize the climate skill with dependencies.

        Args:
            config_obj: Skill configuration from commons
            mqtt_client: MQTT client for device communication
            db_engine: Database engine for global device registry
            template_env: Jinja2 environment for response templates
            task_group: Async task group for concurrent operations
            logger: Logger instance for debugging and monitoring
        """
        # Pass engine to BaseSkill (NEW REQUIRED PARAMETER)
        super().__init__(
            config_obj=config_obj,
            mqtt_client=mqtt_client,
            task_group=task_group,
            engine=db_engine,  # ← NEW REQUIRED PARAMETER
            logger=logger,
        )
        self.db_engine = db_engine
        self.template_env = template_env
        self.intent_to_template: dict[IntentType, jinja2.Template] = {}

        # AIDEV-NOTE: Intent-based configuration replaces calculate_certainty method
        self.supported_intents = {
            IntentType.DEVICE_SET: 0.8,  # "set temperature to 22 degrees"
            IntentType.SYSTEM_HELP: 0.7,  # "how do I control temperature?"
        }

        # AIDEV-NOTE: Device types this skill can control
        self.supported_device_types = ["hvac"]

        # AIDEV-NOTE: Template preloading at init prevents runtime template lookup failures
        self._load_templates()

    def _load_templates(self) -> None:
        """Load and validate all required templates with fallback handling.

        Raises:
            RuntimeError: If critical templates cannot be loaded
        """
        template_mappings = {
            IntentType.SYSTEM_HELP: "help.j2",
            IntentType.DEVICE_SET: "set_temperature.j2",
        }

        failed_templates = []
        for intent_type, template_name in template_mappings.items():
            try:
                self.intent_to_template[intent_type] = self.template_env.get_template(template_name)
            except jinja2.TemplateNotFound as e:
                self.logger.error("Failed to load template %s: %s", template_name, e)
                failed_templates.append(template_name)

        if failed_templates:
            raise RuntimeError(f"Critical templates failed to load: {', '.join(failed_templates)}")

        self.logger.debug("All templates successfully loaded during initialization.")

    async def get_devices(self, rooms: list[str]) -> list[ClimateSkillDevice]:
        """Return devices for a list of rooms from global device registry.

        Args:
            rooms: List of room names to get devices from

        Returns:
            List of ClimateSkillDevice objects for the specified rooms
        """
        self.logger.info("Fetching devices for rooms: %s", rooms)

        # Filter global_devices by room and transform to ClimateSkillDevice
        devices = []
        for global_device in self.global_devices:
            if global_device.room and global_device.room.name in rooms:
                try:
                    climate_device = ClimateSkillDevice.from_global_device(global_device)
                    devices.append(climate_device)
                except ValueError as e:
                    self.logger.warning("Skipping device %s: %s", global_device.name, e)

        self.logger.debug("Found %d devices in rooms %s", len(devices), rooms)
        return devices

    async def find_parameters(
        self, intent_type: IntentType, classified_intent: ClassifiedIntent, current_room: str
    ) -> Parameters:
        """Extract parameters from classified intent entities.

        Args:
            intent_type: The type of intent being processed
            classified_intent: The classified intent with extracted entities
            current_room: The room where the command originated

        Returns:
            Parameters object with devices, rooms, and temperature
        """
        parameters = Parameters()

        # Extract rooms from entities, fallback to current room
        room_entities = classified_intent.entities.get("rooms", [])
        parameters.rooms = [room.normalized_value for room in room_entities] if room_entities else [current_room]

        # Get devices for the target rooms
        devices = await self.get_devices(parameters.rooms)

        if intent_type == IntentType.DEVICE_SET:
            parameters.targets = list(devices)

            # Extract temperature from number entities
            number_entities = classified_intent.entities.get("numbers", [])
            if number_entities:
                # normalized_value for numbers should be the numeric value
                parameters.temperature = int(number_entities[0].normalized_value)
            else:
                self.logger.warning("No temperature value found in DEVICE_SET intent")

        self.logger.debug("Parameters found for intent %s: %s", intent_type, parameters)
        return parameters

    def _render_response(self, intent_type: IntentType, parameters: Parameters) -> str:
        """Render response using template for given intent type.

        Args:
            intent_type: The intent type to render response for
            parameters: Command parameters for template context

        Returns:
            str: Rendered response text
        """
        template = self.intent_to_template.get(intent_type)
        if template:
            answer = template.render(
                intent_type=intent_type,
                parameters=parameters,
            )
            self.logger.debug("Generated answer using template for intent %s.", intent_type)
            return answer
        self.logger.error("No template found for intent %s.", intent_type)
        return "Sorry, I couldn't process your request."

    async def _send_mqtt_commands(self, intent_type: IntentType, parameters: Parameters) -> None:
        """Send MQTT commands to control target devices.

        Args:
            intent_type: The intent type being performed (DEVICE_SET)
            parameters: Command parameters containing target devices and temperature

        Raises:
            Exception: If MQTT publishing fails for any device
        """
        if not parameters.targets:
            self.logger.warning("No target devices to send MQTT commands to")
            return

        for device in parameters.targets:
            if intent_type == IntentType.DEVICE_SET:
                payload = jinja2.Template(device.payload_set_template).render(temperature=parameters.temperature)
            else:
                self.logger.error("Unknown intent type for MQTT command: %s", intent_type)
                continue

            self.logger.info("Sending payload %s to topic %s via MQTT.", payload, device.topic)
            try:
                await self.mqtt_client.publish(device.topic, payload, qos=1)
            except Exception as e:
                self.logger.error("Failed to send MQTT message to topic %s: %s", device.topic, e, exc_info=True)

    async def _handle_device_set(self, intent_request: IntentRequest) -> None:
        """Handle DEVICE_SET intent - set temperature for climate devices.

        Args:
            intent_request: The intent request with classified intent and client request
        """
        classified_intent = intent_request.classified_intent
        client_request = intent_request.client_request
        current_room = client_request.room

        # Extract parameters from entities
        parameters = await self.find_parameters(IntentType.DEVICE_SET, classified_intent, current_room)

        if not parameters.targets:
            await self.send_response("I couldn't find any climate devices in that room.", client_request)
            return

        if parameters.temperature == 0:
            await self.send_response("Please specify a temperature to set.", client_request)
            return

        # Send response and MQTT commands
        answer = self._render_response(IntentType.DEVICE_SET, parameters)
        self.add_task(self.send_response(answer, client_request=client_request))
        self.add_task(self._send_mqtt_commands(IntentType.DEVICE_SET, parameters))

    async def _handle_system_help(self, intent_request: IntentRequest) -> None:
        """Handle SYSTEM_HELP intent - show help information.

        Args:
            intent_request: The intent request with classified intent and client request
        """
        client_request = intent_request.client_request
        current_room = client_request.room

        # Build empty parameters for help template
        parameters = Parameters()
        parameters.rooms = [current_room]

        # Send response
        answer = self._render_response(IntentType.SYSTEM_HELP, parameters)
        self.add_task(self.send_response(answer, client_request=client_request))

    async def process_request(self, intent_request: IntentRequest) -> None:
        """Main request processing method - routes intent to appropriate handler.

        Orchestrates the full command processing pipeline:
        1. Extract intent type from classified intent
        2. Route to appropriate intent handler
        3. Handler extracts entities, controls devices, and sends response

        Args:
            intent_request: The intent request with classified intent and client request
        """
        classified_intent = intent_request.classified_intent
        intent_type = classified_intent.intent_type

        self.logger.debug(
            "Processing intent %s with confidence %.2f",
            intent_type,
            classified_intent.confidence,
        )

        # Route to appropriate handler
        if intent_type == IntentType.DEVICE_SET:
            await self._handle_device_set(intent_request)
        elif intent_type == IntentType.SYSTEM_HELP:
            await self._handle_system_help(intent_request)
        else:
            self.logger.warning("Unsupported intent type: %s", intent_type)
            await self.send_response(
                "I'm not sure how to handle that request.",
                client_request=intent_request.client_request,
            )
