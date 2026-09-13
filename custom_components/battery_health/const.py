"""Constants for Battery Health Analyzer."""

from datetime import timedelta

DOMAIN = "battery_health"
NAME = "Battery Health Analyzer"

ANALYSIS_INTERVAL = timedelta(minutes=30)
HISTORY_WINDOW = timedelta(hours=24)

ISSUE_AMBIGUOUS_BATTERY = "ambiguous_battery"
ISSUE_AMBIGUOUS_BATTERY_LOW = "ambiguous_battery_low"
ISSUE_AMBIGUOUS_LAST_SEEN = "ambiguous_last_seen"
ISSUE_AMBIGUOUS_OUTAGE = "ambiguous_power_outage_count"
ISSUE_AMBIGUOUS_VOLTAGE = "ambiguous_voltage"
ISSUE_MISSING_BATTERY_PERCENT = "missing_battery_percentage"
ISSUE_MISSING_VOLTAGE = "missing_voltage"
