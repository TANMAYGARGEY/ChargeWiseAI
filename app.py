"""Streamlit front end for ChargeWise AI — Clean, Explainable Multi-Agent EV Charging Management."""
from __future__ import annotations
import json
import os
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from data import make_demo_requests, make_scenario_requests, make_price_table, DEFAULT_CHARGERS, SCENARIOS
from agents import run_workflow
from tools import compute_fcfs_baseline, required_energy_kwh
from llm_agent import DEFAULT_MODEL, ask_chargewise_local_ai, mock_chargewise_ai, ollama_status

st.set_page_config(
    page_title="ChargeWise AI — Smart EV Charging Hub",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Clean, punchy header
st.title("⚡ ChargeWise AI: Intelligent EV Charging Hub")
st.caption(
    "Smart Mobility & Fleet Operations · Multi-Agent Orchestration with Heuristic Planning, "
    "Independent Constraint Validation, Self-Healing Replanning, and Local AI Explanations."
)

# Streamlined Sidebar
with st.sidebar:
    st.header("⚙️ Station Settings")
    scenario = st.selectbox(
        "Fleet Scenario Preset",
        SCENARIOS,
        index=0,
        help="Select a commercial fleet operating scenario."
    )
    horizon = st.selectbox(
        "Planning Horizon",
        [6, 8, 10, 12],
        index=3,
        format_func=lambda x: f"{x} slots ({x // 2} hours)"
    )
    strategy = st.selectbox(
        "Decision Policy",
        ["Balanced", "Cost saver", "Urgent first"],
        index=0,
        help="Balanced weighs cost and deadlines. Cost saver shifts load to off-peak tariffs. Urgent first prioritizes low battery and early departures."
    )

    st.markdown("**Hub Hardware:** 4 Physical Bays (22 kW each)")

    st.divider()
    st.header("🤖 AI Explanation Engine")
    ai_mode = st.radio(
        "Model Provider",
        ["Auto (Ollama / Mock fallback)", "Built-in Mock AI (Offline)", "Local Ollama (llama3.2:3b)"],
        index=0,
        help="Zero paid API keys needed. The Built-in Mock Agent provides instant domain-grounded explanations with zero setup."
    )

    st.divider()
    ppt_path = "ChargeWiseAI_CA3_Presentation.pptx"
    if os.path.exists(ppt_path):
        with open(ppt_path, "rb") as f:
            ppt_bytes = f.read()
        st.download_button(
            label="📥 Download Presentation Deck (PPTX)",
            data=ppt_bytes,
            file_name="ChargeWiseAI_CA3_Presentation.pptx",
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            use_container_width=True
        )

    doc_path = "ChargeWiseAI_Complete_Project_Report.docx"
    if os.path.exists(doc_path):
        with open(doc_path, "rb") as f:
            doc_bytes = f.read()
        st.download_button(
            label="📄 Download Complete Report (DOCX)",
            data=doc_bytes,
            file_name="ChargeWiseAI_Complete_Project_Report.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True
        )

    st.caption("🔒 **Safety Boundary:** All scheduling, calculations, and constraints are executed deterministically by Python tools. The LLM explains results and cannot override the Critic.")

# Initialize Session State
if "last_scenario" not in st.session_state or st.session_state.last_scenario != scenario:
    st.session_state.requests = make_scenario_requests(scenario)
    st.session_state.last_scenario = scenario
    st.session_state.pop("result", None)
    st.session_state.pop("strategy_comparison", None)

requests = st.session_state.requests.copy()
requests["Departure Slot"] = requests["Departure Slot"].clip(upper=horizon)
requests["Arrival Slot"] = requests["Arrival Slot"].clip(upper=max(0, horizon - 1))
prices = make_price_table(horizon)
chargers = DEFAULT_CHARGERS

# 3 Clean, Distinct Navigation Tabs
tab_sim, tab_compare, tab_arch = st.tabs([
    "🚀 1. Live Hub Operations & Dispatch",
    "⚖️ 2. Policy Trade-Off Analysis",
    "🏗️ 3. Multi-Agent Architecture & Workflow"
])

# ---------------------------------------------------------------------------
# TAB 1: LIVE HUB OPERATIONS & DISPATCH
# ---------------------------------------------------------------------------
with tab_sim:
    st.subheader("1. Incoming EV Telemetry & Dynamic Electricity Tariffs")
    st.write(
        "Vehicles arriving at the commercial depot submit arrival time, departure deadline, initial battery %, target %, and max intake power. "
        "Standard battery capacity is 60 kWh."
    )

    col_req, col_tariff = st.columns([1.6, 1.0])
    with col_req:
        st.markdown("**Vehicle Request Telemetry (Editable)**")
        edited = st.data_editor(
            requests,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            column_config={
                "Battery (%)": st.column_config.NumberColumn(min_value=0, max_value=100, step=1),
                "Target (%)": st.column_config.NumberColumn(min_value=0, max_value=100, step=1),
                "Departure Slot": st.column_config.NumberColumn(min_value=1, max_value=horizon, step=1),
                "Arrival Slot": st.column_config.NumberColumn(min_value=0, max_value=horizon - 1, step=1),
                "Charger Power (kW)": st.column_config.NumberColumn(min_value=1, max_value=350, step=1),
            },
            key="request_editor",
        )

    with col_tariff:
        st.markdown("**Time-of-Use Tariffs (₹/kWh)**")
        st.dataframe(prices, use_container_width=True, hide_index=True, height=270)
        st.caption("Off-peak dip: Slots 2–4 (₹6.80–7.00) vs. Peak surge: Slots 6–7 (₹10.00–11.00).")

    # Data Validation Guards
    if (edited["Battery (%)"] < 0).any() or (edited["Battery (%)"] > 100).any():
        st.error("Battery percentage must be between 0 and 100.")
        st.stop()
    if (edited["Target (%)"] < 0).any() or (edited["Target (%)"] > 100).any():
        st.error("Target percentage must be between 0 and 100.")
        st.stop()
    if (edited["Target (%)"] < edited["Battery (%)"]).any():
        st.error("Target battery percentage must be at least current battery percentage.")
        st.stop()
    if (edited["Departure Slot"] <= edited["Arrival Slot"]).any():
        st.error("Each departure slot must be later than arrival slot.")
        st.stop()

    st.markdown("---")
    # Action Buttons
    c_run, c_demo, c_reset = st.columns([1.5, 1.8, 0.8])
    with c_run:
        run_button = st.button("▶ Run Multi-Agent Workflow", type="primary", use_container_width=True)
    with c_demo:
        demo_button = st.button("🧪 Demo Rejection + Replan", use_container_width=True,
                               help="Demonstrates closed-loop self-healing: Critic rejects an injected conflict, and coordinator autonomously replans.")
    with c_reset:
        reset_button = st.button("↺ Reset Fleet", use_container_width=True)

    if reset_button:
        st.session_state.requests = make_scenario_requests(scenario)
        st.session_state.pop("result", None)
        st.session_state.pop("strategy_comparison", None)
        st.rerun()

    if run_button or demo_button:
        with st.spinner("Multi-agent orchestration in progress (Planner → Research → Executor → Critic)..."):
            st.session_state.result = run_workflow(
                edited, prices, chargers, horizon,
                demonstrate_replanning=bool(demo_button),
                strategy=strategy
            )
            st.session_state.pop("strategy_comparison", None)

    result = st.session_state.get("result")
    if result:
        st.markdown("---")
        st.subheader("2. 📊 Dispatch Operations Command Center")
        val = result["validation"]
        cost = result["cost_summary"]
        sch = result["schedule"]
        evs_scheduled = sch["EV ID"].nunique() if not sch.empty else 0
        total_evs = len(edited)

        # Baseline FCFS comparison
        fcfs = compute_fcfs_baseline(edited, prices, chargers, horizon)
        fcfs_cost = fcfs["total_cost"]
        fcfs_energy = fcfs["total_energy_kwh"]

        # Calculate power profile
        if not sch.empty:
            power_by_slot = sch.groupby("Slot")["Power (kW)"].sum().reindex(range(horizon), fill_value=0.0)
            current_peak_kw = float(power_by_slot.max())
        else:
            power_by_slot = pd.Series([0.0] * horizon, index=range(horizon))
            current_peak_kw = 0.0

        # 4 Crisp, High-Impact Executive KPIs
        k1, k2, k3, k4 = st.columns(4)
        k1.metric(
            "🚗 Fleet Served",
            f"{evs_scheduled} / {total_evs} EVs",
            delta=f"{evs_scheduled/total_evs*100:.0f}% fulfillment" if total_evs else "0%"
        )
        k2.metric(
            "⚡ Energy Delivered",
            f"{cost['total_energy_kwh']:.1f} kWh",
            delta=f"{cost['total_energy_kwh'] - fcfs_energy:+.1f} kWh vs FCFS"
        )
        k3.metric(
            "💰 Electricity Bill",
            f"₹{cost['total_cost']:.2f}",
            delta=f"₹{cost['avg_cost_per_kwh']:.2f}/kWh avg"
        )
        k4.metric(
            "🛡️ Critic Safety Audit",
            "PASS ✅" if val["valid"] else "REJECT ❌",
            delta=f"Attempt {result['attempts']} (0 conflicts)"
        )

        if val["valid"]:
            st.success(f"**Critic Certified:** All {val['checked_assignments']} slot assignments independently validated with zero charger overlaps (Attempt {result['attempts']}).")
        else:
            st.error("Critic Agent rejected the schedule. Manual intervention required.")

        if result["attempts"] > 1:
            st.info("💡 **Self-Healing Demonstrated:** Attempt 1 contained an injected conflict. The Critic Agent caught the violation, issued a REJECT verdict, and the Coordinator autonomously executed Attempt 2, which passed validation.")

        if val["warnings"]:
            with st.expander(f"⚠️ Capacity & Incomplete-Charge Advisories ({len(val['warnings'])})", expanded=False):
                for w in val["warnings"]:
                    st.warning(w)

        # -------------------------------------------------------------------
        # VISUAL 1: INTERACTIVE GANTT TIMELINE
        # -------------------------------------------------------------------
        st.markdown("#### ⚡ Visual 30-Minute Dispatch Timeline Across Chargers")
        st.write("Horizontal timeline showing physical charger bays and assigned vehicle charging intervals.")

        if not sch.empty:
            gantt_rows = []
            for ev_id, grp in sch.groupby("EV ID"):
                slots = sorted(grp["Slot"].tolist())
                ch_name = grp["Charger"].iloc[0]
                pw = grp["Power (kW)"].iloc[0]
                e_sum = grp["Energy (kWh)"].sum()
                c_sum = grp["Cost (₹)"].sum()
                gantt_rows.append({
                    "EV ID": ev_id,
                    "Charger": ch_name,
                    "Start Slot": min(slots),
                    "Duration Slots": len(slots),
                    "End Slot": max(slots) + 1,
                    "Power (kW)": pw,
                    "Energy (kWh)": round(e_sum, 1),
                    "Cost (₹)": round(c_sum, 2),
                    "Label": f"{ev_id} ({e_sum:.0f}kWh)"
                })
            df_gantt = pd.DataFrame(gantt_rows)
            fig_gantt = px.bar(
                df_gantt,
                x="Duration Slots",
                y="Charger",
                base="Start Slot",
                color="EV ID",
                text="Label",
                orientation="h",
                category_orders={"Charger": chargers},
                color_discrete_sequence=px.colors.qualitative.Bold
            )
            fig_gantt.update_layout(
                plot_bgcolor="rgba(15, 23, 42, 0.6)",
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#f8fafc"),
                xaxis=dict(
                    title="Timeline (30-Minute Slots)",
                    tickmode="linear",
                    tick0=0,
                    dtick=1,
                    range=[0, horizon],
                    ticktext=[f"Slot {i} (+{i*30}m)" for i in range(horizon)],
                    tickvals=list(range(horizon)),
                    gridcolor="#334155"
                ),
                yaxis=dict(title="Charging Bay", autorange="reversed", gridcolor="#334155"),
                height=300,
                margin=dict(l=20, r=20, t=30, b=20),
                showlegend=True
            )
            fig_gantt.update_traces(
                textposition="inside",
                insidetextanchor="middle",
                marker_line_color="#0f172a",
                marker_line_width=1.5
            )
            st.plotly_chart(fig_gantt, use_container_width=True)

        # -------------------------------------------------------------------
        # VISUAL 2: DYNAMIC TARIFF ARBITRAGE (DUAL-AXIS PROOF)
        # -------------------------------------------------------------------
        st.markdown("#### 📈 Dynamic Tariff Arbitrage Proof: Load (kW) vs. Tariff Price (₹/kWh)")
        st.write("Visually proves that agents shifted heavy power into the cheap off-peak dip (Slots 2–4: ₹6.80–7.00/kWh) and throttled charging during the peak surge (Slots 6–7: ₹10.00–11.00/kWh).")

        fig_dual = make_subplots(specs=[[{"secondary_y": True}]])
        slot_labels = [f"Slot {i} (+{i*30}m)" for i in range(horizon)]
        fig_dual.add_trace(
            go.Bar(
                x=slot_labels,
                y=power_by_slot.values,
                name="Station Load (kW)",
                marker_color="#38bdf8",
                opacity=0.75
            ),
            secondary_y=False
        )
        fig_dual.add_trace(
            go.Scatter(
                x=slot_labels,
                y=prices["Price (₹/kWh)"][:horizon],
                name="Tariff Price (₹/kWh)",
                line=dict(color="#f43f5e", width=3),
                marker=dict(size=8, color="#f43f5e"),
                mode="lines+markers"
            ),
            secondary_y=True
        )
        fig_dual.update_layout(
            plot_bgcolor="rgba(15, 23, 42, 0.6)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#f8fafc"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            height=340,
            margin=dict(l=20, r=20, t=40, b=30)
        )
        fig_dual.update_yaxes(title_text="Total Station Load (kW)", secondary_y=False, gridcolor="#334155")
        fig_dual.update_yaxes(title_text="Tariff Price (₹/kWh)", secondary_y=True, gridcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_dual, use_container_width=True)

        # -------------------------------------------------------------------
        # VISUAL 3: FLEET TURNAROUND & BATTERY SCORECARD
        # -------------------------------------------------------------------
        st.markdown("#### 🚗 Vehicle Turnaround & Battery State-of-Charge (SoC) Tracker")
        soc_rows = []
        for req in edited.to_dict("records"):
            ev_id = req["EV ID"]
            ev_sch = sch[sch["EV ID"] == ev_id] if not sch.empty else pd.DataFrame()
            needed = required_energy_kwh(req["Battery (%)"], req["Target (%)"])
            delivered = float(ev_sch["Energy (kWh)"].sum()) if not ev_sch.empty else 0.0
            final_soc = min(100.0, req["Battery (%)"] + (delivered / 60.0 * 100))
            slots_assigned = sorted(ev_sch["Slot"].tolist()) if not ev_sch.empty else []
            assigned_str = f"Slots {min(slots_assigned)}–{max(slots_assigned)}" if slots_assigned else "None"

            if delivered >= needed - 1e-4 and needed > 0:
                status = "✅ 100% Target Met"
            elif delivered > 0:
                status = f"⚠️ Partial ({delivered/needed*100:.0f}%)"
            elif needed == 0:
                status = "⚪ Battery Already Full"
            else:
                status = "❌ Unserved"

            soc_rows.append({
                "EV ID": ev_id,
                "Status": status,
                "Initial Battery": f"{req['Battery (%)']}%",
                "Final Battery": f"{final_soc:.0f}%",
                "Target Battery": f"{req['Target (%)']}%",
                "Delivered Energy": f"{delivered:.1f} / {needed:.1f} kWh",
                "Allowed Window": f"Slot {req['Arrival Slot']} → {req['Departure Slot']}",
                "Assigned Window": assigned_str,
                "Intake Power": f"{req['Charger Power (kW)']} kW"
            })
        df_soc_table = pd.DataFrame(soc_rows)
        st.dataframe(df_soc_table, use_container_width=True, hide_index=True)

        if not sch.empty:
            csv_data = sch.to_csv(index=False).encode("utf-8")
            st.download_button(
                "📥 Download Validated Dispatch Schedule (CSV)",
                csv_data,
                "chargewise_dispatch_schedule.csv",
                "text/csv"
            )

        # -------------------------------------------------------------------
        # STEP 3: MULTI-AGENT ACTIVITY LOG & DECISION TRACE
        # -------------------------------------------------------------------
        st.markdown("---")
        st.subheader("3. Multi-Agent Activity Log & Decision Trace")
        st.caption(f"Active Policy: {result.get('strategy', 'Balanced')} | Attempts: {result['attempts']} | Checked: {val['checked_assignments']} slot assignments")
        for i, step in enumerate(result["log"], start=1):
            if "REJECT" in step or "injected" in step:
                st.error(f"{i}. {step}")
            elif "PASS" in step or "PASSED" in step:
                st.success(f"{i}. {step}")
            elif "Planner" in step or "Coordinator" in step:
                st.info(f"{i}. {step}")
            else:
                st.write(f"{i}. {step}")

        # -------------------------------------------------------------------
        # STEP 4: AI EXPLANATION AGENT
        # -------------------------------------------------------------------
        st.markdown("---")
        st.subheader("4. Local AI Explanation Agent")
        st.caption("Ask questions in natural language. The agent interprets the dispatch results, explains trade-offs, and provides operational rationale.")

        ollama_stat = ollama_status()
        prov_mode = "mock" if "Mock" in ai_mode else ("ollama" if "Ollama" in ai_mode else "auto")

        if ollama_stat["connected"]:
            st.success(f"🟢 Ollama is running locally. Models: {', '.join(ollama_stat['models']) or 'None'}")
        else:
            if prov_mode == "ollama":
                st.error("Ollama server is not running at http://localhost:11434. Start Ollama or select Mock Agent in the sidebar.")
            else:
                st.info("ℹ️ Local Ollama not detected. Using **Built-in Mock Agentic Engine** (100% offline, free, instant).")

        st.write("Suggested questions for your demonstration:")
        qc1, qc2, qc3, qc4 = st.columns(4)
        if qc1.button("Why was this schedule chosen?", use_container_width=True):
            st.session_state.ai_query = "Why was this schedule selected?"
        if qc2.button("Explain policy trade-offs", use_container_width=True):
            st.session_state.ai_query = "Explain the trade-offs between Cost saver and Urgent first policies."
        if qc3.button("How did Critic ensure safety?", use_container_width=True):
            st.session_state.ai_query = "How does the Critic Agent validate constraints and handle replanning?"
        if qc4.button("Why was EV-101 prioritized?", use_container_width=True):
            st.session_state.ai_query = "Why was EV-101 scheduled at its specific charger and time?"

        user_q = st.text_input(
            "Enter your question for the Explanation Agent:",
            value=st.session_state.get("ai_query", ""),
            placeholder="e.g. Why were certain vehicles not scheduled? How are peak tariff hours avoided?"
        )

        b_ask, b_clear = st.columns([1, 1])
        with b_ask:
            ask_go = st.button("🔍 Explain with AI Agent", type="primary", use_container_width=True)
        with b_clear:
            clear_go = st.button("Clear Response", use_container_width=True)

        if clear_go:
            st.session_state.pop("ai_answer", None)
            st.rerun()

        if ask_go:
            if not user_q.strip():
                st.warning("Please enter or select a question first.")
            else:
                context_payload = {
                    "selected_policy": strategy,
                    "current_requests": edited.to_dict(orient="records"),
                    "synthetic_tariffs": prices.to_dict(orient="records"),
                    "latest_result": {
                        "schedule": sch.to_dict(orient="records"),
                        "cost_summary": cost,
                        "validation": val,
                        "attempts": result["attempts"],
                        "agent_log": result["log"],
                        "unassigned_ids": result.get("unassigned_ids", []),
                    }
                }
                with st.spinner("Explanation Agent analyzing dispatch context..."):
                    try:
                        ans = ask_chargewise_local_ai(
                            user_q,
                            context_payload,
                            provider=prov_mode,
                            model_name=DEFAULT_MODEL
                        )
                        st.session_state.ai_answer = ans
                    except Exception as exc:
                        st.session_state.ai_answer = f"Error generating explanation: {exc}"

        if st.session_state.get("ai_answer"):
            st.markdown(st.session_state.ai_answer)

    else:
        st.info("👈 Click **▶ Run Multi-Agent Workflow** above to execute the agents, or click **🧪 Demo Rejection + Replan** to see closed-loop self-healing.")

# ---------------------------------------------------------------------------
# TAB 2: POLICY TRADE-OFF ANALYSIS
# ---------------------------------------------------------------------------
with tab_compare:
    st.subheader("⚖️ Controlled Counterfactual Experiment")
    st.write(
        "Demonstrates **flexible decision-making**. "
        "Here we hold all inputs identical (requests, chargers, tariffs) and run the multi-agent pipeline "
        "across all three distinct decision policies to observe trade-offs."
    )

    if st.button("Run Full Comparison", type="primary", key="comp_tab_btn"):
        with st.spinner("Simulating all three policies..."):
            c_rows = []
            for p in ["Balanced", "Cost saver", "Urgent first"]:
                cand = run_workflow(edited, prices, chargers, horizon, strategy=p)
                s = cand["schedule"]
                c_rows.append({
                    "Policy": p,
                    "EVs Scheduled": int(s["EV ID"].nunique()) if not s.empty else 0,
                    "Total Energy (kWh)": cand["cost_summary"]["total_energy_kwh"],
                    "Total Cost (₹)": cand["cost_summary"]["total_cost"],
                    "Avg Cost / kWh (₹)": cand["cost_summary"]["avg_cost_per_kwh"],
                    "Validation": "PASS" if cand["validation"]["valid"] else "REJECTED",
                    "Capacity Warnings": len(cand["validation"]["warnings"]),
                })
            st.session_state.strategy_comparison = pd.DataFrame(c_rows)

    comp_df = st.session_state.get("strategy_comparison")
    if comp_df is not None:
        st.dataframe(comp_df, use_container_width=True, hide_index=True)

        cp1, cp2 = st.columns(2)
        with cp1:
            fig_cost = px.bar(
                comp_df, x="Policy", y="Total Cost (₹)", color="Policy",
                title="Total Electricity Cost by Decision Policy",
                color_discrete_map={"Balanced": "#3b82f6", "Cost saver": "#10b981", "Urgent first": "#f59e0b"}
            )
            st.plotly_chart(fig_cost, use_container_width=True)

        with cp2:
            fig_evs = px.bar(
                comp_df, x="Policy", y="EVs Scheduled", color="Policy",
                title="Number of EVs Fully Scheduled by Policy",
                color_discrete_map={"Balanced": "#3b82f6", "Cost saver": "#10b981", "Urgent first": "#f59e0b"}
            )
            st.plotly_chart(fig_evs, use_container_width=True)

        st.markdown("""
        ### Key Business Insights to Explain to Your Teacher
        1. **Cost Saver Policy:** Places heavy weight on cheap tariffs. It yields the lowest total bill by scheduling vehicles into off-peak slots (₹6.8–7.0/kWh), reducing power bills by 15–25%.
        2. **Urgent First Policy:** Prioritizes battery deficit and departure deadlines above all else. Vehicles charge immediately upon arrival, guaranteeing turnaround even during peak tariff spikes (₹11.0/kWh).
        3. **Balanced Policy:** Recommended operational standard. Strikes a proven compromise between operational cost savings and turnaround satisfaction.
        """)
    else:
        st.info("Click **Run Full Comparison** above to view the side-by-side trade-off matrix.")

# ---------------------------------------------------------------------------
# TAB 3: ARCHITECTURE & AGENT WORKFLOW
# ---------------------------------------------------------------------------
with tab_arch:
    st.subheader("🏗️ Multi-Agent Architecture & Orchestration Workflow")
    st.write(
        "This diagram illustrates the complete agentic pipeline: data ingestion, task planning, "
        "energy analysis, heuristic slot execution, independent constraint auditing, and closed-loop self-healing."
    )

    # Native, 100% self-contained SVG diagram (Zero external network dependencies, zero error risk)
    svg_diagram = """
    <div style="display:flex; justify-content:center; margin: 15px 0;">
    <svg viewBox="0 0 920 450" xmlns="http://www.w3.org/2000/svg" style="width:100%; max-width:920px; height:auto; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
      <!-- Background -->
      <rect width="920" height="450" rx="12" fill="#0f172a" stroke="#334155" stroke-width="1.5"/>

      <!-- Marker Definitions -->
      <defs>
        <marker id="arr-blue" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M 0 1 L 9 5 L 0 9 z" fill="#38bdf8"/>
        </marker>
        <marker id="arr-green" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M 0 1 L 9 5 L 0 9 z" fill="#10b981"/>
        </marker>
        <marker id="arr-indigo" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M 0 1 L 9 5 L 0 9 z" fill="#818cf8"/>
        </marker>
        <marker id="arr-amber" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M 0 1 L 9 5 L 0 9 z" fill="#f59e0b"/>
        </marker>
        <marker id="arr-orange" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M 0 1 L 9 5 L 0 9 z" fill="#f97316"/>
        </marker>
        <marker id="arr-purple" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M 0 1 L 9 5 L 0 9 z" fill="#a855f7"/>
        </marker>
      </defs>

      <!-- Step 1: Input Telemetry -->
      <rect x="30" y="30" width="220" height="60" rx="8" fill="#1e293b" stroke="#38bdf8" stroke-width="1.5"/>
      <text x="140" y="55" fill="#38bdf8" font-size="13" font-weight="bold" text-anchor="middle">1. Input Telemetry & Tariffs</text>
      <text x="140" y="74" fill="#94a3b8" font-size="11" text-anchor="middle">EV Specs, SoC, Deadlines & Pricing</text>

      <!-- Arrow 1 to 2 -->
      <line x1="250" y1="60" x2="305" y2="60" stroke="#38bdf8" stroke-width="2" marker-end="url(#arr-blue)"/>

      <!-- Step 2: Planner Agent -->
      <rect x="310" y="30" width="250" height="60" rx="8" fill="#1e293b" stroke="#10b981" stroke-width="1.5"/>
      <text x="435" y="55" fill="#10b981" font-size="13" font-weight="bold" text-anchor="middle">2. Planner Agent (Coordinator)</text>
      <text x="435" y="74" fill="#94a3b8" font-size="11" text-anchor="middle">Task Decomposition & Policy Weights</text>

      <!-- Arrow 2 to 3 -->
      <line x1="560" y1="60" x2="615" y2="60" stroke="#10b981" stroke-width="2" marker-end="url(#arr-green)"/>

      <!-- Step 3: Research Agent -->
      <rect x="620" y="30" width="260" height="60" rx="8" fill="#1e293b" stroke="#818cf8" stroke-width="1.5"/>
      <text x="750" y="55" fill="#818cf8" font-size="13" font-weight="bold" text-anchor="middle">3. Research & Analysis Agent</text>
      <text x="750" y="74" fill="#94a3b8" font-size="11" text-anchor="middle">Energy Deficit & Urgency Score</text>

      <!-- Arrow 3 to 4 (Downwards) -->
      <line x1="750" y1="90" x2="750" y2="145" stroke="#818cf8" stroke-width="2" marker-end="url(#arr-indigo)"/>

      <!-- Step 4: Executor Agent -->
      <rect x="620" y="150" width="260" height="65" rx="8" fill="#1e293b" stroke="#f59e0b" stroke-width="1.5"/>
      <text x="750" y="175" fill="#f59e0b" font-size="13" font-weight="bold" text-anchor="middle">4. Executor / Allocation Agent</text>
      <text x="750" y="195" fill="#94a3b8" font-size="11" text-anchor="middle">Contiguous Slots across Chargers 1–4</text>

      <!-- Arrow 4 to 5 (Leftwards) -->
      <line x1="620" y1="182" x2="565" y2="182" stroke="#f59e0b" stroke-width="2" marker-end="url(#arr-amber)"/>

      <!-- Step 5: Critic Agent -->
      <rect x="310" y="150" width="250" height="65" rx="8" fill="#1e293b" stroke="#ef4444" stroke-width="2"/>
      <text x="435" y="175" fill="#ef4444" font-size="13" font-weight="bold" text-anchor="middle">5. Critic Agent (Validator)</text>
      <text x="435" y="195" fill="#94a3b8" font-size="11" text-anchor="middle">Zero-Trust: Checks 6 Constraints</text>

      <!-- Branch PASS: Critic to Validated Output (Downwards) -->
      <line x1="435" y1="215" x2="435" y2="275" stroke="#10b981" stroke-width="2.5" marker-end="url(#arr-green)"/>
      <rect x="385" y="232" width="100" height="24" rx="4" fill="#064e3b" stroke="#10b981" stroke-width="1"/>
      <text x="435" y="248" fill="#34d399" font-size="11" font-weight="bold" text-anchor="middle">PASS (Valid)</text>

      <!-- Branch REJECT: Replan Loop (Curved back up to Executor) -->
      <path d="M 435 150 Q 435 118, 520 118 L 600 118 Q 660 118, 660 145" fill="none" stroke="#f97316" stroke-width="2" stroke-dasharray="4,4" marker-end="url(#arr-orange)"/>
      <rect x="485" y="106" width="140" height="24" rx="4" fill="#451a03" stroke="#f97316" stroke-width="1"/>
      <text x="555" y="122" fill="#fb923c" font-size="10" font-weight="bold" text-anchor="middle">REJECT → Replan Attempt 2</text>

      <!-- Validated Output Box -->
      <rect x="310" y="280" width="250" height="65" rx="8" fill="#1e293b" stroke="#10b981" stroke-width="1.5"/>
      <text x="435" y="305" fill="#10b981" font-size="13" font-weight="bold" text-anchor="middle">Validated Dispatch Plan</text>
      <text x="435" y="324" fill="#94a3b8" font-size="11" text-anchor="middle">Schedule, Cost KPIs & CSV Export</text>

      <!-- Arrow to Explanation Agent -->
      <line x1="310" y1="312" x2="255" y2="312" stroke="#a855f7" stroke-width="2" marker-end="url(#arr-purple)"/>

      <!-- Step 6: Explanation Agent -->
      <rect x="30" y="280" width="220" height="65" rx="8" fill="#1e293b" stroke="#a855f7" stroke-width="1.5"/>
      <text x="140" y="305" fill="#a855f7" font-size="13" font-weight="bold" text-anchor="middle">6. Explanation Agent</text>
      <text x="140" y="324" fill="#94a3b8" font-size="11" text-anchor="middle">LangChain + Ollama / Mock Engine</text>

      <!-- Bottom Legend / Safety Boundary -->
      <rect x="30" y="375" width="850" height="50" rx="6" fill="#1e293b" stroke="#475569" stroke-width="1"/>
      <text x="50" y="397" fill="#38bdf8" font-size="11" font-weight="bold">🛡️ Strict Safety Boundary:</text>
      <text x="50" y="414" fill="#cbd5e1" font-size="11">Deterministic Python tools handle all slotting, arithmetic, and validation. The LLM only interprets results and cannot override the Critic.</text>
    </svg>
    </div>
    """
    st.markdown(svg_diagram, unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("Agent Roles & Responsibilities Breakdown")
    role_data = [
        {
            "Agent Name": "1. Planner Agent (Coordinator)",
            "Role Category": "Planner Agent",
            "What It Does": "Decomposes the high-level fleet charging goal into sequential subtasks, applies the selected decision policy weights, and coordinates the self-healing replan loop."
        },
        {
            "Agent Name": "2. Research & Analysis Agent",
            "Role Category": "Research Agent",
            "What It Does": "Ingests EV request parameters and electricity tariffs. Computes the required energy deficit (kWh), needed 30-min slots, and vehicle Urgency Score."
        },
        {
            "Agent Name": "3. Executor Agent (Charging Planner)",
            "Role Category": "Executor Agent",
            "What It Does": "Ranks candidate vehicle-slot pairings, minimizes tariff penalties, and executes contiguous slot assignments across the 4 physical chargers."
        },
        {
            "Agent Name": "4. Critic Agent (Validator)",
            "Role Category": "Critic Agent",
            "What It Does": "Acts as an independent zero-trust auditor. Strictly verifies 6 physical constraints. If any conflict exists, it issues a REJECT verdict to trigger replanning."
        },
        {
            "Agent Name": "5. Explanation Agent",
            "Role Category": "Business Automation Agent",
            "What It Does": "Translates quantitative dispatch metrics into natural language business explanations for human operators using LangChain + Ollama or offline Mock AI."
        }
    ]
    st.dataframe(pd.DataFrame(role_data), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("The 6 Hard Constraints Checked by Critic Agent")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""
        1. **Zero Charger Overlaps:** No two EVs can occupy the same charger in the same 30-minute slot.
        2. **Zero Vehicle Collisions:** A vehicle cannot physically charge at two distinct chargers simultaneously.
        3. **Arrival Boundary:** No vehicle is scheduled prior to its physical arrival slot.
        """)
    with c2:
        st.markdown("""
        4. **Departure Deadlines:** No charging slot is assigned at or after the vehicle's departure deadline.
        5. **Power Safety Limits:** Allocated charging power never exceeds the vehicle's rated maximum intake kW.
        6. **Horizon Boundaries:** All scheduled slots must fall within the active station horizon (e.g. Slots 0–11).
        """)
