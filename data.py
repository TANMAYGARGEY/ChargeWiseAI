"""Synthetic EV requests and electricity prices for the ChargeWise AI demo."""
from __future__ import annotations
import pandas as pd

SLOT_MINUTES = 30
SLOT_HOURS = SLOT_MINUTES / 60
DEFAULT_CHARGERS = ["Charger 1", "Charger 2", "Charger 3", "Charger 4"]

SCENARIOS = [
    "Standard Fleet (Balanced 10 EVs)",
    "Morning Rush (Tight Deadlines)",
    "Delivery Vans (Critical Low Battery)",
]

def make_demo_requests() -> pd.DataFrame:
    """Return repeatable dummy requests. Values are illustrative, not live data."""
    rows = [
        # id, battery %, target %, departure slot, max kW, arrival slot
        ("EV-101", 12, 70, 3, 22, 0),   # urgent: must leave in 90 minutes
        ("EV-102", 35, 80, 6, 11, 0),
        ("EV-103", 68, 90, 8, 7, 1),
        ("EV-104", 20, 85, 5, 22, 0),
        ("EV-105", 55, 75, 7, 11, 2),
        ("EV-106", 42, 90, 10, 7, 0),
        ("EV-107", 15, 60, 4, 22, 1),
        ("EV-108", 72, 95, 9, 11, 0),
        ("EV-109", 30, 75, 6, 7, 2),
        ("EV-110", 50, 85, 10, 22, 1),
    ]
    return pd.DataFrame(rows, columns=[
        "EV ID", "Battery (%)", "Target (%)", "Departure Slot",
        "Charger Power (kW)", "Arrival Slot"
    ])

def make_scenario_requests(scenario_name: str) -> pd.DataFrame:
    """Return synthetic requests customized for different fleet scenarios."""
    if "Morning Rush" in scenario_name:
        rows = [
            ("EV-201", 15, 80, 3, 22, 0),  # leaves in 1.5 hrs
            ("EV-202", 20, 75, 4, 22, 0),
            ("EV-203", 25, 70, 3, 22, 0),
            ("EV-204", 30, 85, 4, 11, 0),
            ("EV-205", 40, 90, 5, 22, 1),
            ("EV-206", 50, 80, 6, 11, 1),
            ("EV-207", 18, 65, 3, 22, 0),
            ("EV-208", 60, 90, 6, 7, 1),
        ]
    elif "Delivery Vans" in scenario_name:
        rows = [
            ("VAN-01", 8, 90, 5, 22, 0),   # critically low
            ("VAN-02", 10, 85, 4, 22, 0),
            ("VAN-03", 14, 80, 6, 22, 0),
            ("VAN-04", 12, 75, 4, 22, 1),
            ("VAN-05", 22, 85, 7, 11, 1),
            ("VAN-06", 28, 90, 8, 11, 0),
            ("VAN-07", 35, 80, 9, 7, 2),
            ("VAN-08", 15, 70, 5, 22, 1),
        ]
    else:
        return make_demo_requests()

    return pd.DataFrame(rows, columns=[
        "EV ID", "Battery (%)", "Target (%)", "Departure Slot",
        "Charger Power (kW)", "Arrival Slot"
    ])

def make_price_table(horizon_slots: int = 12) -> pd.DataFrame:
    """Simulated tariff per kWh. Slot 0 is the current half-hour."""
    prices = [8.5, 8.0, 7.2, 6.8, 7.0, 9.5, 11.0, 10.0, 8.0, 7.0, 6.5, 7.5]
    if horizon_slots > len(prices):
        prices.extend([8.0] * (horizon_slots - len(prices)))
    return pd.DataFrame({
        "Slot": list(range(horizon_slots)),
        "Time": [f"+{i * 30}–+{(i + 1) * 30} min" for i in range(horizon_slots)],
        "Price (₹/kWh)": prices[:horizon_slots],
    })
