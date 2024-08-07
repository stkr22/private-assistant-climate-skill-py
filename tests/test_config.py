import pathlib

import pytest
import yaml
from private_assistant_climate_skill.config import (
    SkillConfig,
)
from pydantic import ValidationError

# Sample invalid YAML configuration (missing required fields)
invalid_yaml = """
mqtt_server_host: "test_host"
mqtt_server_port: "invalid_port"  # invalid type
client_id: 12345  # invalid type
"""


def test_load_valid_config():
    data_directory = pathlib.Path(__file__).parent / "data" / "config.yaml"
    with data_directory.open("r") as file:
        config_data = yaml.safe_load(file)
    config = SkillConfig.model_validate(config_data)

    assert config.home_assistant_api_url == "http://localhost.local/api"
    assert config.home_assistant_token == "test_token"


def test_load_invalid_config():
    config_data = yaml.safe_load(invalid_yaml)
    with pytest.raises(ValidationError):
        SkillConfig.model_validate(config_data)
