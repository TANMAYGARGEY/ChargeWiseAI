"""Deterministic tools used by the agents."""
from __future__ import annotations
import math
from typing import Any
import pandas as pd
from data import SLOT_HOURS, DEFAULT_CHARGERS

BATTERY_CAPACITY_KWH = 60.0  # illustrative common battery size for the demo

def required_energy_kwh(battery_pct: float, target_pct: float,
                        capacity_kwh: float = BATTERY_CAPACITY_KWH) -> float:
    """Energy needed to move from current battery % to target battery %."""
    return max(0.0, (float(target_pct) - float(battery_pct)) / 100.0 * capacity_kwh)

def required_slots(battery_pct: float, target_pct: float, power_kw: float) -> int:
    """Number of 30-minute slots needed, assuming constant charging power."""
    energy = required_energy_kwh(battery_pct, target_pct)
    if energy <= 0:
        return 0
    if power_kw <= 0:
        raise ValueError("Charger power must be greater than zero.")
    energy_per_slot = power_kw * SLOT_HOURS
    return max(1, math.ceil(energy / energy_per_slot))

def slot_energy_kwh(power_kw: float) -> float:
    return float(power_kw) * SLOT_HOURS

def estimate_session_cost(power_kw: float, slots: list[int],
                          prices: pd.DataFrame) -> float:
    price_by_slot = prices.set_index("Slot")["Price (₹/kWh)"].to_dict()
    return sum(slot_energy_kwh(power_kw) * float(price_by_slot[s]) for s in slots)

def validate_schedule(schedule: pd.DataFrame, requests: pd.DataFrame,
                      prices: pd.DataFrame, chargers: list[str],
                      horizon_slots: int) -> dict[str, Any]:
    """Independent validation: checks assignment, overlaps, deadlines, and totals."""
    errors: list[str] = []
    if schedule.empty:
        return {"valid": True, "errors": [], "warnings": ["No sessions were scheduled."],
                "checked_assignments": 0}

    required_cols = {"EV ID", "Charger", "Slot", "Power (kW)", "Energy (kWh)", "Cost (₹)"}
    if not required_cols.issubset(schedule.columns):
        return {"valid": False, "errors": ["Schedule is missing required columns."],
                "warnings": [], "checked_assignments": 0}

    req_by_id = requests.set_index("EV ID").to_dict("index")
    # A charger can serve only one EV in any slot.
    dup_charger = schedule.duplicated(["Charger", "Slot"], keep=False)
    if dup_charger.any():
        errors.append("Charger conflict: more than one EV is assigned to the same charger and slot.")
    # One EV cannot be assigned to two chargers at once.
    dup_ev = schedule.duplicated(["EV ID", "Slot"], keep=False)
    if dup_ev.any():
        errors.append("Vehicle conflict: an EV is assigned to more than one charger in the same slot.")

    for row in schedule.to_dict("records"):
        ev_id, charger, slot = row["EV ID"], row["Charger"], int(row["Slot"])
        if ev_id not in req_by_id:
            errors.append(f"Unknown vehicle in schedule: {ev_id}.")
            continue
        if charger not in chargers:
            errors.append(f"{ev_id} uses unknown charger {charger}.")
        req = req_by_id[ev_id]
        if slot < int(req["Arrival Slot"]):
            errors.append(f"{ev_id} is scheduled before its arrival.")
        if slot >= int(req["Departure Slot"]):
            errors.append(f"{ev_id} is scheduled at or after its departure deadline.")
        if slot < 0 or slot >= horizon_slots:
            errors.append(f"{ev_id} has an out-of-horizon slot.")
        if float(row["Power (kW)"]) > float(req["Charger Power (kW)"]):
            errors.append(f"{ev_id} is assigned more power than its configured limit.")

    # Compare energy delivered with each EV's requested energy.
    for ev_id, req in req_by_id.items():
        ev_rows = schedule[schedule["EV ID"] == ev_id]
        needed = required_energy_kwh(req["Battery (%)"], req["Target (%)"])
        delivered = float(ev_rows["Energy (kWh)"].sum()) if not ev_rows.empty else 0.0
        if delivered + 1e-6 < needed:
            # An EV may be infeasible under the station's constraints. This is a warning,
            # not a schedule-invalid error, so the app can show it as unserved.
            pass

    warnings = []
    for ev_id, req in req_by_id.items():
        ev_rows = schedule[schedule["EV ID"] == ev_id]
        needed = required_energy_kwh(req["Battery (%)"], req["Target (%)"])
        delivered = float(ev_rows["Energy (kWh)"].sum()) if not ev_rows.empty else 0.0
        if delivered + 1e-6 < needed:
            warnings.append(
                f"{ev_id}: requested {needed:.1f} kWh, scheduled {delivered:.1f} kWh; "
                "not all requested energy fits in the available slots."
            )
    return {
        "valid": len(errors) == 0,
        "errors": sorted(set(errors)),
        "warnings": warnings,
        "checked_assignments": int(len(schedule)),
    }

def compute_fcfs_baseline(requests: pd.DataFrame, prices: pd.DataFrame,
                          chargers: list[str], horizon_slots: int) -> dict[str, Any]:
    """Simulate uncoordinated First-Come-First-Serve (FCFS) baseline for quantitative comparison.

    Vehicles plug in and charge immediately upon arrival on the first available charger,
    ignoring electricity tariffs and urgency heuristics.
    """
    price_by_slot = prices.set_index("Slot")["Price (₹/kWh)"].to_dict()
    occupied: set[tuple[str, int]] = set()
    rows: list[dict] = []

    # Sort strictly by arrival time (FIFO)
    ordered = requests.sort_values(["Arrival Slot", "Departure Slot"], ascending=[True, True])

    for req in ordered.to_dict("records"):
        ev_id = req["EV ID"]
        power = float(req["Charger Power (kW)"])
        duration = required_slots(req["Battery (%)"], req["Target (%)"], power)
        if duration <= 0:
            continue
        arrival = int(req["Arrival Slot"])
        departure = int(req["Departure Slot"])
        energy_per_slot = slot_energy_kwh(power)

        # FCFS attempts to start immediately at arrival
        max_start = min(departure - duration, horizon_slots - duration)
        assigned = False
        for start in range(arrival, max_start + 1):
            block = range(start, start + duration)
            for charger in chargers:
                if all((charger, s) not in occupied for s in block):
                    for s in block:
                        occupied.add((charger, s))
                        rows.append({
                            "EV ID": ev_id, "Charger": charger, "Slot": s,
                            "Power (kW)": power, "Energy (kWh)": energy_per_slot,
                            "Cost (₹)": energy_per_slot * float(price_by_slot.get(s, 8.0))
                        })
                    assigned = True
                    break
            if assigned:
                break

    df = pd.DataFrame(rows)
    if df.empty:
        return {
            "total_cost": 0.0, "total_energy_kwh": 0.0, "peak_kw": 0.0,
            "scheduled_count": 0, "schedule": df
        }

    # Calculate peak simultaneous power (kW)
    power_by_slot = df.groupby("Slot")["Power (kW)"].sum()
    peak_kw = float(power_by_slot.max()) if not power_by_slot.empty else 0.0
    total_cost = float(df["Cost (₹)"].sum())
    total_energy = float(df["Energy (kWh)"].sum())
    scheduled_count = int(df["EV ID"].nunique())

    return {
        "total_cost": round(total_cost, 2),
        "total_energy_kwh": round(total_energy, 2),
        "peak_kw": round(peak_kw, 1),
        "scheduled_count": scheduled_count,
        "schedule": df
    }

