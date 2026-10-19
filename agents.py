"""ChargeWise AI agents and orchestration.

Implements the multi-agent architecture required by CA-3:
1. Planner Agent (Coordinator): Decomposes goal into ordered subtasks, manages policies and replanning.
2. Research & Analysis Agent: Ingests telemetry, computes required energy, slot duration, and urgency scores.
3. Executor Agent (Charging Planner): Evaluates candidate slot combinations against policy heuristics and executes assignments.
4. Energy Cost Tool: Estimates per-slot and aggregated tariff expenses.
5. Critic Agent (Validator): Zero-trust verification of 6 hard constraints with error diagnostics.
"""
from __future__ import annotations
import math
import pandas as pd
from data import SLOT_HOURS, DEFAULT_CHARGERS
from tools import (
    required_energy_kwh, required_slots, slot_energy_kwh,
    estimate_session_cost, validate_schedule
)

def request_analysis_agent(requests: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Research & Analysis Agent: calculates requested energy, duration, and urgency features."""
    result = requests.copy()
    result["Energy Needed (kWh)"] = result.apply(
        lambda r: required_energy_kwh(r["Battery (%)"], r["Target (%)"]), axis=1
    )
    result["Slots Needed"] = result.apply(
        lambda r: required_slots(r["Battery (%)"], r["Target (%)"], r["Charger Power (kW)"]), axis=1
    )
    result["Time Available (slots)"] = result["Departure Slot"] - result["Arrival Slot"]
    result["Feasible Alone"] = result["Slots Needed"] <= result["Time Available (slots)"]
    result["Urgency Score"] = result.apply(
        lambda r: urgency_score(r["Battery (%)"], r["Time Available (slots)"],
                                r["Slots Needed"]), axis=1
    )
    log = [
        f"Research Agent: ingested and analyzed {len(result)} EV requests.",
        f"Research Agent: {int(result['Feasible Alone'].sum())}/{len(result)} requests can fit full charge in their arrival-departure windows before charger competition.",
    ]
    return result, log

# Alias for CA-3 nomenclature
research_agent = request_analysis_agent

def urgency_score(battery_pct: float, time_available: float, slots_needed: float) -> float:
    """Higher score means more urgent; transparent heuristic, not a black-box model."""
    low_battery = (100.0 - float(battery_pct)) / 100.0
    tight_deadline = 1.0 / max(1.0, float(time_available))
    charge_pressure = float(slots_needed) / max(1.0, float(time_available))
    return round(0.45 * low_battery + 0.30 * tight_deadline + 0.25 * charge_pressure, 4)

def _candidate_score(start: int, duration: int, urgency: float,
                     arrival: int, departure: int, prices: pd.DataFrame,
                     strategy: str = "Balanced") -> float:
    """Lower is better. Strategy changes the cost/wait/urgency trade-off."""
    p = prices.set_index("Slot")["Price (₹/kWh)"].to_dict()
    avg_price = sum(float(p[s]) for s in range(start, start + duration)) / duration
    wait_slots = start - arrival
    # Explicit policy weights make the agent's decision policy inspectable.
    wait_component = wait_slots / max(1, departure - arrival)
    deadline_slack = departure - (start + duration)
    if strategy == "Cost saver":
        price_weight, wait_weight, urgency_weight = 1.8, 0.20, 0.45
    elif strategy == "Urgent first":
        price_weight, wait_weight, urgency_weight = 0.45, 0.15, 3.2
    else:  # Balanced
        price_weight, wait_weight, urgency_weight = 1.0, 0.55, 1.8
    price_component = (avg_price / 12.0) * price_weight
    urgency_bonus = urgency * urgency_weight * (1.0 / (1 + max(0, deadline_slack)))
    return price_component + wait_weight * wait_component - urgency_bonus

def charging_planner_agent(analysis: pd.DataFrame, prices: pd.DataFrame,
                           chargers: list[str], horizon_slots: int,
                           log: list[str] | None = None,
                           strategy: str = "Balanced") -> tuple[pd.DataFrame, list[str]]:
    """Executor Agent (Charging Planner): assigns contiguous half-hour slots across chargers."""
    events = list(log or [])
    events.append(f"Executor Agent: applying '{strategy}' decision policy weights.")
    # Different policies change the priority order as well as candidate scoring.
    if strategy == "Cost saver":
        ordered = analysis.sort_values(["Arrival Slot", "Departure Slot", "Energy Needed (kWh)"],
                                       ascending=[True, True, False])
    elif strategy == "Urgent first":
        ordered = analysis.sort_values(["Urgency Score", "Departure Slot", "Arrival Slot"],
                                       ascending=[False, True, True])
    else:
        ordered = analysis.sort_values(["Urgency Score", "Departure Slot", "Arrival Slot"],
                                       ascending=[False, True, True])
    occupied: set[tuple[str, int]] = set()
    ev_occupied: set[tuple[str, int]] = set()
    rows: list[dict] = []
    price_by_slot = prices.set_index("Slot")["Price (₹/kWh)"].to_dict()

    for req in ordered.to_dict("records"):
        ev_id = req["EV ID"]
        duration = int(req["Slots Needed"])
        if duration <= 0:
            events.append(f"Executor Agent: {ev_id} already meets target; no charging required.")
            continue
        arrival, departure = int(req["Arrival Slot"]), int(req["Departure Slot"])
        possible: list[tuple[float, str, int]] = []
        max_start = min(departure - duration, horizon_slots - duration)
        for start in range(arrival, max_start + 1):
            block = range(start, start + duration)
            for charger in chargers:
                if all((charger, s) not in occupied and (ev_id, s) not in ev_occupied for s in block):
                    score = _candidate_score(start, duration, float(req["Urgency Score"]),
                                             arrival, departure, prices, strategy)
                    possible.append((score, charger, start))
        if not possible:
            events.append(
                f"Executor Agent: no complete contiguous {duration}-slot session fits for {ev_id}; left unassigned."
            )
            continue

        _, charger, start = min(possible, key=lambda x: (x[0], x[2], x[1]))
        slots = list(range(start, start + duration))
        power = float(req["Charger Power (kW)"])
        energy_per_slot = slot_energy_kwh(power)
        for slot in slots:
            occupied.add((charger, slot))
            ev_occupied.add((ev_id, slot))
            rows.append({
                "EV ID": ev_id, "Charger": charger, "Slot": slot,
                "Time": prices.loc[prices["Slot"] == slot, "Time"].iloc[0],
                "Power (kW)": power, "Energy (kWh)": energy_per_slot,
                "Price (₹/kWh)": float(price_by_slot[slot]),
                "Cost (₹)": energy_per_slot * float(price_by_slot[slot]),
                "Urgency Score": float(req["Urgency Score"]),
            })
        events.append(
            f"Executor Agent: assigned {ev_id} to {charger}, slots {slots[0]}–{slots[-1]} "
            f"({duration} half-hour slot(s))."
        )

    schedule = pd.DataFrame(rows)
    if not schedule.empty:
        schedule = schedule.sort_values(["Slot", "Charger", "EV ID"]).reset_index(drop=True)
    events.append("Executor Agent: schedule assignment completed.")
    return schedule, events

# Alias for CA-3 nomenclature
executor_agent = charging_planner_agent

def energy_cost_agent(schedule: pd.DataFrame) -> tuple[dict, list[str]]:
    """Energy Cost Tool / Agent: calculates aggregated energy and tariff costs."""
    if schedule.empty:
        summary = {"total_energy_kwh": 0.0, "total_cost": 0.0, "avg_cost_per_kwh": 0.0}
    else:
        energy = float(schedule["Energy (kWh)"].sum())
        cost = float(schedule["Cost (₹)"].sum())
        summary = {
            "total_energy_kwh": round(energy, 2),
            "total_cost": round(cost, 2),
            "avg_cost_per_kwh": round(cost / energy, 2) if energy else 0.0,
        }
    return summary, [
        f"Cost Tool: total scheduled energy = {summary['total_energy_kwh']:.1f} kWh.",
        f"Cost Tool: estimated station electricity cost = ₹{summary['total_cost']:.2f}."
    ]

def critic_agent(schedule: pd.DataFrame, requests: pd.DataFrame,
                 prices: pd.DataFrame, chargers: list[str],
                 horizon_slots: int) -> tuple[dict, list[str]]:
    """Critic Agent (Validator): independently verifies 6 hard constraints using deterministic tools."""
    result = validate_schedule(schedule, requests, prices, chargers, horizon_slots)
    if result["valid"]:
        message = f"Critic Agent: PASS — independently verified {result['checked_assignments']} slot assignments with zero conflicts."
    else:
        message = "Critic Agent: REJECT — " + " ".join(result["errors"])
    return result, [message]

def run_workflow(requests: pd.DataFrame, prices: pd.DataFrame,
                 chargers: list[str] | None = None, horizon_slots: int = 12,
                 demonstrate_replanning: bool = False,
                 strategy: str = "Balanced") -> dict:
    """Planner Agent (Coordinator): Orchestrates task plan -> research -> execute -> cost -> critique -> replan."""
    chargers = chargers or DEFAULT_CHARGERS
    log = [
        "Planner Agent: received EV charging objective; decomposed into subtask pipeline.",
        "Planner Agent: Task 1: Research EV urgency -> Task 2: Rank requests -> Task 3: Execute slot allocation -> Task 4: Cost estimation -> Task 5: Critic validation.",
        f"Planner Agent: configured decision policy = '{strategy}'.",
    ]
    analysis, analysis_log = request_analysis_agent(requests)
    log.extend(analysis_log)
    schedule, planner_log = charging_planner_agent(
        analysis, prices, chargers, horizon_slots, log, strategy
    )
    log = planner_log
    # Demo switch intentionally injects a conflict into the first attempt so the
    # Critic can visibly reject it. It is only an educational demonstration of self-healing.
    if demonstrate_replanning and not schedule.empty:
        corrupted = schedule.copy()
        first = corrupted.iloc[0].copy()
        bad = first.copy()
        bad["EV ID"] = str(corrupted.iloc[-1]["EV ID"])
        bad["Charger"] = str(first["Charger"])
        bad["Slot"] = int(first["Slot"])
        bad["Time"] = str(first["Time"])
        corrupted = pd.concat([corrupted, pd.DataFrame([bad])], ignore_index=True)
        schedule = corrupted
        log.append("Planner Agent: [DEMO MODE] injected synthetic charger conflict into Attempt 1 to test Critic Agent.")

    cost_summary, cost_log = energy_cost_agent(schedule)
    validation, critic_log = critic_agent(schedule, requests, prices, chargers, horizon_slots)
    log.extend(cost_log)
    log.extend(critic_log)
    attempts = 1

    if not validation["valid"]:
        log.append("Planner Agent: Critic rejected schedule; activating conditional self-healing replan loop.")
        # Rebuild from original requests and price data. The deterministic planner
        # generates a clean schedule; the fault injection is not repeated.
        analysis, _ = request_analysis_agent(requests)
        schedule, planner_log2 = charging_planner_agent(
            analysis, prices, chargers, horizon_slots, [], strategy
        )
        log.extend(["Planner Agent: Replanning Attempt 2 initiated."] + planner_log2)
        cost_summary, cost_log = energy_cost_agent(schedule)
        validation, critic_log = critic_agent(schedule, requests, prices, chargers, horizon_slots)
        log.extend(cost_log + critic_log)
        attempts = 2
        log.append("Planner Agent: Replan Attempt 2 PASSED validation." if validation["valid"]
                   else "Planner Agent: Replan still invalid; alerting human operator.")

    scheduled_ids = set(schedule["EV ID"].unique()) if not schedule.empty else set()
    requested_ids = set(requests["EV ID"].astype(str))
    unassigned = sorted(requested_ids - scheduled_ids)
    return {
        "analysis": analysis,
        "schedule": schedule,
        "cost_summary": cost_summary,
        "validation": validation,
        "log": log,
        "attempts": attempts,
        "unassigned_ids": unassigned,
        "prices": prices,
        "chargers": chargers,
        "horizon_slots": horizon_slots,
        "strategy": strategy,
    }
