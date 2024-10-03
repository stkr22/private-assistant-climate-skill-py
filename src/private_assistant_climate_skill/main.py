import pathlib
from typing import Annotated

import jinja2
import typer
from private_assistant_commons import async_typer, mqtt_connection_handler, skill_config, skill_logger
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from private_assistant_climate_skill import climate_skill

app = async_typer.AsyncTyper()


@app.async_command()
async def start_skill(
    config_path: Annotated[pathlib.Path, typer.Argument(envvar="PRIVATE_ASSISTANT_CONFIG_PATH")],
):
    # Set up logger early on
    logger = skill_logger.SkillLogger.get_logger("Private Assistant ClimateSkill")

    # Load configuration
    config_obj = skill_config.load_config(config_path, skill_config.SkillConfig)

    # Create an async database engine
    db_engine_async = create_async_engine(skill_config.PostgresConfig.from_env().connection_string_async)

    # Create tables asynchronously
    async with db_engine_async.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    # Set up Jinja2 template environment
    template_env = jinja2.Environment(
        loader=jinja2.PackageLoader(
            "private_assistant_climate_skill",
            "templates",
        )
    )

    # Start the skill using the async MQTT connection handler
    await mqtt_connection_handler.mqtt_connection_handler(
        climate_skill.ClimateSkill,
        config_obj,
        retry_interval=5,
        logger=logger,
        template_env=template_env,
        db_engine=db_engine_async,
    )


if __name__ == "__main__":
    start_skill(config_path=pathlib.Path("./local_config.yaml"))
