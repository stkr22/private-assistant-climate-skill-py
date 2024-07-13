import logging
from pathlib import Path

import private_assistant_commons as commons
import yaml
from pydantic import ValidationError

logger = logging.getLogger(__name__)


class SkillConfig(commons.SkillConfig):
    home_assistant_api_url: str
    home_assistant_token: str


def load_config(config_path: Path) -> SkillConfig:
    try:
        with config_path.open("r") as file:
            config_data = yaml.safe_load(file)
        return SkillConfig.model_validate(config_data)
    except FileNotFoundError as err:
        logger.error("Config file not found: %s", config_path)
        raise err
    except ValidationError as err_v:
        logger.error("Validation error: %s", err_v)
        raise err_v
