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


# Test for help.j2
@pytest.mark.parametrize(
    "expected_output",
    [
        ("Here is how you can use the ClimateSkill:\n" "- Say 'set the temperature to 22 degrees' to set a device."),
    ],
)
def test_help_template(jinja_env, expected_output):
    result = render_template("help.j2", Parameters(), jinja_env)
    assert result == expected_output


# Test for set_temperature.j2 (setting temperature)
@pytest.mark.parametrize(
    "targets, temperature, expected_output",
    [
        (
            [ClimateSkillDevice(alias="Living Room Thermostat")],
            22,
            "I have set the temperature to 22°C.",
        ),
        (
            [ClimateSkillDevice(alias="Bedroom Thermostat")],
            18,
            "I have set the temperature to 18°C.",
        ),
    ],
)
def test_set_temperature_template(jinja_env, targets, temperature, expected_output):
    parameters = Parameters(targets=targets, temperature=temperature)
    result = render_template("set_temperature.j2", parameters, jinja_env)
    assert result == expected_output
