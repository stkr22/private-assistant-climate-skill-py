import logging
import os
import pathlib
import sys
from typing import Annotated

import jinja2
import paho.mqtt.client as mqtt
import spacy
import typer
from homeassistant_api import Client

from private_assistant_climate_skill import climate_skill, config

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)

app = typer.Typer()


@app.command()
def start_skill(
    config_path: Annotated[
        pathlib.Path, typer.Argument(envvar="PRIVATE_ASSISTANT_CONFIG_PATH")
    ],
):
    config_obj = config.load_config(config_path)
    climate_skill_obj = climate_skill.ClimateSkill(
        mqtt_client=mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=config_obj.client_id,
            protocol=mqtt.MQTTv5,
        ),
        config_obj=config_obj,
        nlp_model=spacy.load(config_obj.spacy_model),
        ha_api_client=Client(
            config_obj.home_assistant_api_url,
            config_obj.home_assistant_token,
        ),
        template_env=jinja2.Environment(
            loader=jinja2.PackageLoader(
                "private_assistant_climate_skill",
                "templates",
            ),
        ),
    )
    climate_skill_obj.run()


if __name__ == "__main__":
    start_skill(config_path=pathlib.Path("./local_config.yaml"))
