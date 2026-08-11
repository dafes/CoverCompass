"""Constants for CoverCompass."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "cover_compass"
MANUFACTURER: Final = "CoverCompass"
PLATFORMS: Final = ["binary_sensor", "button", "sensor", "switch"]
CONFIG_VERSION: Final = 2
STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = f"{DOMAIN}.overrides"

CONF_HOUSE_NAME: Final = "house_name"
CONF_LATITUDE: Final = "latitude"
CONF_LONGITUDE: Final = "longitude"
CONF_TIME_ZONE: Final = "time_zone"
CONF_HOUSE_ROTATION: Final = "house_rotation"
CONF_COVERS: Final = "covers"
CONF_GLOBAL_ENABLED: Final = "global_enabled"
CONF_DRY_RUN: Final = "dry_run"
CONF_RECONCILE_INTERVAL: Final = "reconcile_interval"

DEFAULT_HOUSE_NAME: Final = "Home"
DEFAULT_RECONCILE_INTERVAL: Final = 300
DEFAULT_EXPOSURE_ANGLE: Final = 55.0
DEFAULT_MIN_ELEVATION: Final = 5.0
DEFAULT_SHADE_POSITION: Final = 25
DEFAULT_NORMAL_POSITION: Final = 100
DEFAULT_MIN_MOVEMENT_INTERVAL: Final = 300
DEFAULT_ACTIVATION_DELAY: Final = 300
DEFAULT_CLEAR_DELAY: Final = 600
DEFAULT_COMMAND_TIMEOUT: Final = timedelta(minutes=3)
POSITION_TOLERANCE: Final = 2

ATTR_CURRENT_POSITION: Final = "current_position"
ATTR_CURRENT_TILT_POSITION: Final = "current_tilt_position"
ATTR_SUPPORTED_FEATURES: Final = "supported_features"

SUN_ENTITY_ID: Final = "sun.sun"
