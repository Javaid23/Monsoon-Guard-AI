"""MonsoonGuard AI — verification and results viewer.

CLAUDE.md section 11: the app only reads saved results and the registry. It
never trains, never writes, and never recomputes science. Anything it shows
comes from reports/, records/ or data/processed/, so what you see here is
exactly what the pipeline produced.

Run with:
    streamlit run src/app/streamlit_app.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.audit.registry_client import MockRegistry  # noqa: E402
from src.audit.verify import Status, verify_record  # noqa: E402

RECORDS = ROOT / "records"
REPORTS = ROOT / "reports"

STATUS_COLOUR = {
    Status.VERIFIED: "#1a7f37",
    Status.COMMITTED: "#0969da",
    Status.OUTCOME_RECORDED: "#0969da",
    Status.PENDING: "#9a6700",
    Status.MISMATCH: "#cf222e",
    Status.MISSED_COMMIT: "#cf222e",
}
TRUST_COLOUR = {"HIGH": "#1a7f37", "MEDIUM": "#9a6700", "LOW": "#cf222e"}


# --- loading (cached; the app is read-only) ---------------------------------

@st.cache_data
def load_registry_entries() -> dict:
    reg = MockRegistry(RECORDS / "mock_registry.json")
    return {k: v.__dict__ for k, v in reg.entries.items()}


@st.cache_data
def load_records() -> dict[str, dict]:
    out = {}
    for p in sorted(RECORDS.glob("PK-*.json")):
        if p.name.endswith(".outcome.json"):
            continue
        out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
    return out


@st.cache_data
def load_outcome(forecast_id: str) -> dict | None:
    p = RECORDS / ("%s.outcome.json" % forecast_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


@st.cache_data
def load_evaluation() -> dict | None:
    p = REPORTS / "evaluation_results.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def registry() -> MockRegistry:
    return MockRegistry(RECORDS / "mock_registry.json")


# --- pages -------------------------------------------------------------------

def page_verification():
    st.header("Verify a forecast record")
    st.write(
        "Paste a forecast id, or pick one below. The app recomputes the "
        "SHA-256 of the published record and compares it with the hash held "
        "in the registry, then checks the commit happened before the rain "
        "window opened."
    )

    records = load_records()
    if not records:
        st.warning("No records found in `records/`. Run "
                   "`python scripts/build_records.py` first.")
        return

    ids = sorted(records)
    chosen = st.selectbox("Forecast id", ids)
    pasted = st.text_input("...or paste an id", value="")
    forecast_id = pasted.strip() or chosen

    record = records.get(forecast_id)
    if record is None:
        st.error("No published record with id `%s`." % forecast_id)
        return

    entry = registry().get(record["forecast_id"])
    outcome = load_outcome(forecast_id)
    result = verify_record(record, entry, outcome)

    colour = STATUS_COLOUR.get(result.status, "#57606a")
    st.markdown(
        "<div style='padding:0.75rem 1rem;border-radius:0.5rem;"
        "background:%s;color:white;font-size:1.25rem;font-weight:600'>%s</div>"
        % (colour, result.status.value), unsafe_allow_html=True)

    st.subheader("Checks")
    for name, passed, detail in result.checks:
        st.write(("✅ " if passed else "❌ ") + "**%s** — %s" % (name, detail))

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Hashes")
        st.code("recomputed: %s\nregistry:   %s"
                % (result.computed_hash, result.registry_hash or "—"))
    with c2:
        st.subheader("Timing")
        if entry:
            st.code("committed:    %s\nwindow opens: %s\nwindow closes: %s\n"
                    "outcome:      %s"
                    % (entry.committed_utc, entry.window_start_utc,
                       entry.window_end_utc, entry.outcome_recorded_utc or "—"))

    st.subheader("Published record")
    st.json(record)
    if outcome:
        st.subheader("Outcome record")
        st.json(outcome)

    st.info(
        "**What this proves:** this exact record existed, unchanged, before "
        "its rain window opened. **What it does not prove:** that the "
        "forecast was good, or that every forecast made was submitted. An "
        "owner-controlled registry cannot demonstrate the absence of "
        "omissions."
    )

    with st.expander("Try tampering with it"):
        st.write("Change a value and watch verification fail — this is the "
                 "whole point of the audit layer.")
        field = st.selectbox("Field to alter",
                             ["p_wrong_bp", "signal_bp", "trust_label"])
        current = record[field]
        new_value = st.text_input("New value", value=str(current))
        if st.button("Re-verify with the altered value"):
            tampered = dict(record)
            tampered[field] = (int(new_value) if field.endswith("_bp")
                               else new_value)
            bad = verify_record(tampered, entry, outcome)
            if bad.status == Status.MISMATCH:
                st.error("MISMATCH — the altered record does not match the "
                         "committed hash. Detected, as intended.")
            else:
                st.warning("Status: %s" % bad.status.value)
            st.code("altered record hash: %s\ncommitted hash:      %s"
                    % (bad.computed_hash, entry.forecast_hash if entry else "—"))


def page_trust():
    st.header("Trust view")
    records = load_records()
    if not records:
        st.warning("No records yet.")
        return

    rows = []
    for fid, rec in records.items():
        outcome = load_outcome(fid)
        rows.append({
            "forecast_id": rec["forecast_id"],
            "issue_time": rec["issue_time_utc"][:10],
            "lead_hours": rec["lead_hours"],
            "signal %": rec["signal_bp"] / 100,
            "p_wrong %": rec["p_wrong_bp"] / 100,
            "trust": rec["trust_label"],
            "event occurred": outcome["event_occurred"] if outcome else None,
            "decision wrong": outcome["decision_was_wrong"] if outcome else None,
        })
    table = pd.DataFrame(rows).sort_values(["issue_time", "lead_hours"])

    st.dataframe(table, use_container_width=True, hide_index=True)

    st.subheader("What the trust label means")
    st.write(
        "`p_wrong` is the calibrated probability that the forecast decision "
        "is wrong. The bands are frozen at HIGH below 20%, MEDIUM 20–50%, "
        "LOW at or above 50%."
    )
    st.warning(
        "**Known problem.** On the evaluated split the false-alarm base rate "
        "is about 57%, so a calibrated model essentially never outputs "
        "`p_wrong` below 20% and the HIGH band is empty. The bands are "
        "reported as frozen, and their poor positioning is recorded as a "
        "finding rather than quietly re-tuned after seeing results. Read the "
        "continuous `p_wrong` column, not just the label."
    )


def page_evaluation():
    st.header("Evaluation results")
    ev = load_evaluation()
    if ev is None:
        st.warning("No `reports/evaluation_results.json`. Run "
                   "`python scripts/run_evaluation.py`.")
        return

    st.caption("Split used for these numbers — train %s, calibrate %s, test %s"
               % (ev["split"]["train"], ev["split"]["calibration"],
                  ev["split"]["test"]))

    st.subheader("Error models")
    rows = []
    for target, d in ev["targets"].items():
        for variant, m in d["metrics"].items():
            rows.append({
                "target": target, "variant": variant,
                "n": m["n"], "positives": m["n_positive"],
                "Brier": round(m["brier"], 4),
                "Brier skill": round(m["brier_skill_vs_reference"], 4),
                "ROC AUC": round(m["roc_auc"], 3) if m["roc_auc"] else None,
                "PR AUC": round(m["pr_auc"], 3) if m["pr_auc"] else None,
            })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(
        "A positive Brier skill score beats climatology; negative is worse "
        "than it. The uncalibrated variants show why calibration is a "
        "separate stage: class weighting buys ranking at the cost of "
        "calibrated probabilities."
    )

    st.subheader("Trust band error rates")
    for target, d in ev["targets"].items():
        st.write("**%s**" % target)
        band_rows = [{
            "band": b, "n": v["n"],
            "share of samples %": round(100 * v["share_of_samples"], 1),
            "error rate %": (round(100 * v["error_rate"], 2)
                             if v["n"] else None),
            "share of all errors %": round(100 * v["share_of_all_errors"], 1),
        } for b, v in d["trust_bands"].items()]
        st.dataframe(pd.DataFrame(band_rows), use_container_width=True,
                     hide_index=True)

    st.subheader("Forecast verification (the underlying forecast)")
    verif = ev.get("forecast_verification_test", {})
    vrows = [{
        "signal level": level,
        "POD": round(d["probability_of_detection"], 3),
        "FAR": round(d["false_alarm_ratio"], 3),
        "CSI": round(d["critical_success_index"], 3),
        "miss rate": round(d["miss_rate"], 3),
        "bias": round(d["frequency_bias"], 3),
    } for level, d in sorted(verif.items())]
    st.dataframe(pd.DataFrame(vrows), use_container_width=True, hide_index=True)

    st.subheader("Uncertainty")
    for target, d in ev["targets"].items():
        ci = d["test_error_rate_ci"]
        st.write("**%s** — error rate %.4f, 95%% CI [%.4f, %.4f] "
                 "(block bootstrap over %d calendar weeks)"
                 % (target, ci["point_estimate"], ci["ci_low"], ci["ci_high"],
                    ci["n_blocks"]))
    st.warning(
        "Intervals are wide because the test split is short. Comparisons "
        "between models at this sample size are not statistically meaningful."
    )


def page_ledger():
    st.header("Ledger status")
    entries = load_registry_entries()
    if not entries:
        st.warning("Registry is empty.")
        return

    with_outcome = sum(1 for e in entries.values() if e.get("outcome_hash"))
    c1, c2, c3 = st.columns(3)
    c1.metric("Records committed", len(entries))
    c2.metric("Outcomes recorded", with_outcome)
    c3.metric("Awaiting outcome", len(entries) - with_outcome)

    rows = [{
        "forecast_id": e["forecast_id"],
        "committed": e["committed_utc"],
        "window opens": e["window_start_utc"],
        "window closes": e["window_end_utc"],
        "outcome recorded": e.get("outcome_recorded_utc") or "—",
    } for e in entries.values()]
    st.dataframe(pd.DataFrame(rows).sort_values("committed"),
                 use_container_width=True, hide_index=True)

    st.info(
        "These records were committed to a **local mock registry** using a "
        "simulated clock set to each forecast's historical issue time, "
        "because they replay archived forecasts rather than live ones. The "
        "registry enforces the same rules as the contract: no commit once a "
        "window has opened, no outcome before it closes. A testnet "
        "deployment was not reached."
    )


PAGES = {
    "Verify a record": page_verification,
    "Trust view": page_trust,
    "Evaluation results": page_evaluation,
    "Ledger status": page_ledger,
}


def main():
    st.set_page_config(page_title="MonsoonGuard AI", page_icon="🌧", layout="wide")
    st.sidebar.title("MonsoonGuard AI")
    st.sidebar.caption("Detecting unreliable extreme rainfall forecasts "
                       "in Pakistan")
    choice = st.sidebar.radio("Page", list(PAGES))
    st.sidebar.markdown("---")
    st.sidebar.caption(
        "Read-only. Everything shown comes from saved pipeline output. "
        "See `reports/report.md` for results and limitations."
    )
    PAGES[choice]()


main()
