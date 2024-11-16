import jinja2
import pytest

from private_assistant_climate_skill.climate_skill import Parameters
from private_assistant_climate_skill.models import ClimateSkillDevice


# Fixture to set up the Jinja2 environment
@pytest.fixture(scope="module")
def jinja_env():
    return jinja2.Environment(
        loader=jinja2.PackageLoader(
            "private_assistant_climate_skill",
            "templates",
        ),
    )


def render_template(template_name, parameters, env, action=None):
    template = env.get_template(template_name)
    return template.render(parameters=parameters, action=action)


# Test for set_temperature.j2 (setting temperature)
@pytest.mark.parametrize(
    "targets, rooms, temperature, expected_output",
    [
        (
            [ClimateSkillDevice(alias="Living Room Thermostat")],
            ["Living Room"],
            22,
            "The temperature has been set to 22 Celsius for the room Living Room.",
        ),
        (
            [ClimateSkillDevice(alias="Bedroom Thermostat")],
            ["Bedroom"],
            18,
            "The temperature has been set to 18 Celsius for the room Bedroom.",
        ),
        (
            [
                ClimateSkillDevice(alias="Living Room Thermostat"),
                ClimateSkillDevice(alias="Bedroom Thermostat"),
            ],
            ["Living Room", "Bedroom"],
            20,
            "The temperature has been set to 20 Celsius for the rooms Living Room, Bedroom.",
        ),
        (
            [],
            [],
            25,
            "The temperature has been set to 25 Celsius.",
        ),
    ],
)
def test_set_temperature_template(jinja_env, targets, rooms, temperature, expected_output):
    parameters = Parameters(targets=targets, rooms=rooms, temperature=temperature)
    result = render_template("set_temperature.j2", parameters, jinja_env)
    assert result == expected_output
