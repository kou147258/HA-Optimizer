"""Constants for HA Optimizer."""
DOMAIN = "ha_optimizer"
VERSION = "1.7.28"

# Config keys
CONF_SCAN_INTERVAL_DAYS = "scan_interval_days"
CONF_STALE_DAYS_THRESHOLD = "stale_days_threshold"
CONF_ENABLE_SOFT_DELETE = "enable_soft_delete"
CONF_SOFT_DELETE_DAYS = "soft_delete_days"
CONF_EXCLUDE_DEVICE_CLASSES = "exclude_device_classes"

# Defaults
DEFAULT_SCAN_INTERVAL_DAYS = 7
DEFAULT_STALE_DAYS_THRESHOLD = 30
DEFAULT_ENABLE_SOFT_DELETE = True
DEFAULT_SOFT_DELETE_DAYS = 7
DEFAULT_EXCLUDE_DEVICE_CLASSES = "smoke,moisture,gas,carbon_monoxide,carbon_dioxide,safety,tamper,door,window,lock,motion,occupancy,vibration,sound"

# Risk levels
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

# Category types
CAT_ENTITY = "entity"
CAT_AUTOMATION = "automation"
CAT_SCRIPT = "script"
CAT_HELPER = "helper"

# Suspicious naming patterns
SUSPICIOUS_PATTERNS = ["test", "demo", "temp", "copy", "backup", "old", "unused", "delete", "tmp"]

# Safety device classes - NEVER auto-suggest deletion
SAFETY_DEVICE_CLASSES = {
    "smoke", "moisture", "gas", "carbon_monoxide", "carbon_dioxide",
    "safety", "tamper", "door", "window", "lock", "motion", "occupancy",
    "vibration", "sound", "battery", "problem", "update", "connectivity"
}

# Store keys
STORE_KEY = f"{DOMAIN}_data"
SOFT_DELETE_STORE_KEY = f"{DOMAIN}_soft_delete"

# Services
SERVICE_SCAN = "scan"
SERVICE_PURGE = "purge"
SERVICE_RESTORE = "restore"
SERVICE_RESTORE_ALL = "restore_all"
SERVICE_EMPTY_TRASH = "empty_trash"
SERVICE_GET_RESULTS = "get_results"

# Events
EVENT_SCAN_COMPLETE = f"{DOMAIN}_scan_complete"
EVENT_PURGE_COMPLETE = f"{DOMAIN}_purge_complete"

# Fingerprint
FINGERPRINT_STORE_KEY = f"{DOMAIN}_fingerprint"
SERVICE_ANALYZE_FINGERPRINT = "analyze_fingerprint"
# Automation run health. Reads the trace component, which keeps the last 5
# runs of every automation (DEFAULT_STORED_TRACES in trace/const.py).
SERVICE_ANALYZE_AUTOMATION_RUNS = "analyze_automation_runs"
SERVICE_COLLECT_BASELINE = "collect_baseline"

# Panel URL
PANEL_URL = "ha-optimizer"
PANEL_TITLE = "Optimizer"
PANEL_ICON = "mdi:broom"
