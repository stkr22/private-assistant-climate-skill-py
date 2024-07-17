import logging

import private_assistant_commons as commons

logger = logging.getLogger(__name__)


class SkillConfig(commons.SkillConfig):
    home_assistant_api_url: str
    home_assistant_token: str
