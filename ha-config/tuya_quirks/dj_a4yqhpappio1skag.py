"""Quirk for Merkury MBW7-1004-4PK smart bulbs (product_id a4yqhpappio1skag).

The Tuya cloud model for these bulbs omits the core light datapoints
(switch, brightness, color temperature, and color), so Home Assistant
creates no light entity even though the bulbs are online and fully
controllable through the Tuya/Smart Life apps.

Observed live behavior (via tuya_sharing MQTT reports):

- Writes with the standard v2 dj codes are accepted by the cloud, which
  converts them to the device's native datapoints/scales.
- The device reports state on non-standard datapoint IDs:
  - DP 83: switch (Boolean)
  - DP 85: brightness (Integer, device scale 0-255)
  - DP 86: color temperature (Integer, kelvin-linear 0-1000 over
    2000-6500 K)
  - DP 88: color as a 4-hex-digit "hhss" string (h and s each 0-255)

Home Assistant expects the v2 codes with mired-inverted temp on a
0-1000 scale and JSON h/s/v color, so the report converters below
rescale device-native values back into the declared v2 ranges.
"""

import json

from tuya_device_handlers import TUYA_QUIRKS_REGISTRY
from tuya_device_handlers.builder import DeviceQuirk
from tuya_device_handlers.builder.device_quirk import (
    DatapointDefinition,
    LocalConvertStrategy,
)
from tuya_device_handlers.const import DPMode, DPType
from tuya_sharing.strategy import strategy

_MIN_KELVIN = 2000
_MAX_KELVIN = 6500
_MIN_MIRED = 1_000_000 / _MAX_KELVIN
_MAX_MIRED = 1_000_000 / _MIN_KELVIN


def _status_key(config_item: dict) -> str:
    key, _ = json.loads(config_item["statusFormat"]).popitem()
    return key


@strategy.register("merkury_dj_bright")
def _convert_bright(dp_item: tuple, config_item: dict = None) -> tuple:
    """Device brightness 0-255 -> bright_value_v2 10-1000."""
    _dp_key, dp_value = dp_item
    key = _status_key(config_item)
    if dp_value is None:
        return key, dp_value
    return key, max(10, min(1000, round(int(dp_value) * 1000 / 255)))


@strategy.register("merkury_dj_temp")
def _convert_temp(dp_item: tuple, config_item: dict = None) -> tuple:
    """Device kelvin-linear 0-1000 -> mired-inverted temp_value_v2 0-1000."""
    _dp_key, dp_value = dp_item
    key = _status_key(config_item)
    if dp_value is None:
        return key, dp_value
    kelvin = _MIN_KELVIN + int(dp_value) / 1000 * (_MAX_KELVIN - _MIN_KELVIN)
    mired = 1_000_000 / kelvin
    value = round((_MAX_MIRED - mired) / (_MAX_MIRED - _MIN_MIRED) * 1000)
    return key, max(0, min(1000, value))


@strategy.register("merkury_dj_colour")
def _convert_colour(dp_item: tuple, config_item: dict = None) -> tuple:
    """Device 'hhss' hex color -> colour_data_v2 JSON h/s/v."""
    _dp_key, dp_value = dp_item
    key = _status_key(config_item)
    if not isinstance(dp_value, str) or len(dp_value) < 4:
        return key, None
    h = round(int(dp_value[0:2], 16) * 360 / 255)
    s = round(int(dp_value[2:4], 16) * 1000 / 255)
    return key, json.dumps({"h": h, "s": s, "v": 1000})


_COLOUR_DATA_V2_VALUES = json.dumps(
    {
        "h": {"min": 0, "scale": 0, "unit": "", "max": 360, "step": 1},
        "s": {"min": 0, "scale": 0, "unit": "", "max": 1000, "step": 1},
        "v": {"min": 0, "scale": 0, "unit": "", "max": 1000, "step": 1},
    }
)

quirk = (
    DeviceQuirk()
    .applies_to(product_id="a4yqhpappio1skag")
    .add_dpid_boolean(
        dpid=83,
        dpcode="switch_led",
        dpmode=DPMode.READ | DPMode.WRITE,
    )
    .add_dpid_integer(
        dpid=85,
        dpcode="bright_value_v2",
        dpmode=DPMode.READ | DPMode.WRITE,
        unit="",
        min=10,
        max=1000,
        scale=0,
        step=1,
    )
    .add_dpid_integer(
        dpid=86,
        dpcode="temp_value_v2",
        dpmode=DPMode.READ | DPMode.WRITE,
        unit="",
        min=0,
        max=1000,
        scale=0,
        step=1,
    )
)

# The builder has no add_dpid_json helper, so append the color datapoint
# definition directly.
quirk._datapoint_definitions.append(  # noqa: SLF001
    DatapointDefinition(
        dpid=88,
        dpcode="colour_data_v2",
        dpmode=DPMode.READ | DPMode.WRITE,
        dptype=DPType.JSON,
        values=_COLOUR_DATA_V2_VALUES,
    )
)

# Device reports use native scales; override the report converters so
# incoming MQTT status maps back into the declared v2 ranges.
quirk._local_strategy.update(  # noqa: SLF001
    {
        (85, "bright_value_v2"): LocalConvertStrategy(
            dpid=85,
            dpcode="bright_value_v2",
            value_convert="merkury_dj_bright",
        ),
        (86, "temp_value_v2"): LocalConvertStrategy(
            dpid=86,
            dpcode="temp_value_v2",
            value_convert="merkury_dj_temp",
        ),
        (88, "colour_data_v2"): LocalConvertStrategy(
            dpid=88,
            dpcode="colour_data_v2",
            value_convert="merkury_dj_colour",
        ),
    }
)

quirk.register(TUYA_QUIRKS_REGISTRY)
