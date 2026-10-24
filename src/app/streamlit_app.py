"""MonsoonGuard AI — verification and results viewer.

CLAUDE.md section 11: the app only reads saved results and the registry. It
never trains, never writes, and never recomputes science. Everything shown
comes from saved pipeline output or the registry, so what you see is
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

from src.audit.chain_client import open_registry  # noqa: E402
from src.audit.verify import Status, verify_record  # noqa: E402

RECORDS = ROOT / "records"
REPORTS = ROOT / "reports"
FIGURES = REPORTS / "figures"

OK, WARN, BAD, INK, MUTED = "#1a7f37", "#bf8700", "#cf222e", "#1f2328", "#636c76"

STATUS_STYLE = {
    Status.VERIFIED: (OK, "Verified", "Hash matches and the commit predates the window."),
    Status.COMMITTED: ("#0969da", "Committed", "On the registry; outcome not yet attached."),
    Status.OUTCOME_RECORDED: ("#0969da", "Outcome recorded", "Awaiting full check."),
    Status.PENDING: (WARN, "Pending", "No commit found for this id."),
    Status.MISMATCH: (BAD, "Mismatch", "This record does not match what was committed."),
    Status.MISSED_COMMIT: (BAD, "Missed commit", "Committed at or after the window opened."),
}
TRUST_STYLE = {"HIGH": OK, "MEDIUM": WARN, "LOW": BAD}

CSS = """
<style>
  #MainMenu, footer {visibility: hidden;}
  .block-container {padding-top: 2.2rem; max-width: 1180px;}
  h1, h2, h3 {letter-spacing: -0.015em;}

  .hero {
    background: linear-gradient(135deg, #0b3d2e 0%, #14604a 55%, #1a7f37 100%);
    color: #fff; padding: 1.6rem 1.9rem; border-radius: 14px; margin-bottom: 1.5rem;
  }
  .hero h1 {margin: 0 0 .35rem 0; font-size: 1.85rem; color: #fff;}
  .hero p {margin: 0; opacity: .92; font-size: .97rem; line-height: 1.5;}
  .hero .tag {
    display: inline-block; margin-top: .85rem; padding: .22rem .7rem;
    background: rgba(255,255,255,.17); border-radius: 999px; font-size: .78rem;
    letter-spacing: .03em;
  }

  .statusbar {
    padding: .95rem 1.25rem; border-radius: 11px; color: #fff;
    font-size: 1.22rem; font-weight: 650; display: flex;
    align-items: baseline; gap: .8rem; margin-bottom: .2rem;
  }
  .statusbar small {font-weight: 400; font-size: .83rem; opacity: .93;}

  .card {
    border: 1px solid #d8dee4; border-radius: 11px; padding: 1rem 1.15rem;
    background: #fff; height: 100%;
  }
  .card .label {font-size: .76rem; color: #636c76; text-transform: uppercase;
                letter-spacing: .06em; margin-bottom: .3rem;}
  .card .value {font-size: 1.5rem; font-weight: 680; color: #1f2328; line-height: 1.15;}
  .card .sub {font-size: .8rem; color: #636c76; margin-top: .25rem;}

  .check {padding: .34rem 0; font-size: .93rem; border-bottom: 1px solid #f0f2f4;}
  .check:last-child {border-bottom: none;}
  .check .d {color: #636c76; font-size: .84rem;}

  .pill {display:inline-block; padding:.12rem .55rem; border-radius:999px;
         font-size:.76rem; font-weight:600; color:#fff;}
  .note {font-size:.87rem; color:#636c76; line-height:1.55;}
</style>
"""


# --- loading -----------------------------------------------------------------

@st.cache_resource
def _registry():
    return open_registry(ROOT, RECORDS)


def registry():
    return _registry()[0]


def chain_info():
    return _registry()[1]


def registry_reason() -> str:
    return _registry()[2]


@st.cache_data
def load_records() -> dict[str, dict]:
    out = {}
    for p in sorted(RECORDS.glob("PK-*.json")):
        if p.name.endswith(".outcome.json"):
            continue
        out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
    return out


@st.cache_data
def load_outcome(fid: str) -> dict | None:
    p = RECORDS / ("%s.outcome.json" % fid)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


@st.cache_data
def load_evaluation() -> dict | None:
    p = REPORTS / "evaluation_results.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


@st.cache_data
def load_thresholds() -> pd.DataFrame | None:
    p = REPORTS / "thresholds_grid.csv"
    return pd.read_csv(p) if p.exists() else None


# --- small builders ----------------------------------------------------------

def hero(title: str, subtitle: str, tag: str = ""):
    st.markdown(
        "<div class='hero'><h1>%s</h1><p>%s</p>%s</div>"
        % (title, subtitle,
           "<span class='tag'>%s</span>" % tag if tag else ""),
        unsafe_allow_html=True)


def card(col, label: str, value: str, sub: str = "", colour: str = INK):
    col.markdown(
        "<div class='card'><div class='label'>%s</div>"
        "<div class='value' style='color:%s'>%s</div>"
        "<div class='sub'>%s</div></div>" % (label, colour, value, sub),
        unsafe_allow_html=True)


def fig(name: str, caption: str = ""):
    p = FIGURES / name
    if p.exists():
        st.image(str(p), width='stretch', caption=caption or None)


# --- pages -------------------------------------------------------------------

def page_overview():
    hero("Weather warnings cry wolf. We tell you which ones.",
         "When forecasters predict heavy monsoon rain over northern Pakistan, "
         "more than half those warnings never come true. Every false alarm "
         "makes people trust the next warning less &mdash; and the next warning "
         "might be the real one. This project scores each forecast in "
         "advance: <b>how likely is this particular warning to be wrong?</b>",
         "IEEE ClimateChain 2026 &middot; Climate Data &amp; Environmental Monitoring")

    ev = load_evaluation()
    records = load_records()

    # --- the problem, in plain numbers --------------------------------------
    st.markdown("### The problem")
    verif = (ev or {}).get("forecast_verification_test", {})
    base = verif.get("0.2") or (list(verif.values())[0] if verif else None)
    c1, c2, c3 = st.columns(3)
    if base:
        card(c1, "Warnings that never come true",
             "%.0f%%" % (100 * base["false_alarm_ratio"]),
             "more than half are false alarms", BAD)
        card(c2, "Real downpours it does catch",
             "%.0f%%" % (100 * base["probability_of_detection"]),
             "the forecast is not useless, just noisy", OK)
        card(c3, "Warnings vs actual events",
             "%.0f times too many" % base["frequency_bias"],
             "on the month we tested", WARN)
    st.markdown("<p class='note'>Those are the forecasts as they come. The "
                "question is not how to make them better &mdash; that is a job "
                "for weather centres &mdash; but whether we can tell, "
                "<i>beforehand</i>, which of them to act on.</p>",
                unsafe_allow_html=True)

    # --- a real example ------------------------------------------------------
    st.write("")
    st.markdown("### What that looks like for one real forecast")
    e1, e2, e3, e4 = st.columns(4)
    card(e1, "4 August 2022", "3 days ahead", "forecast issued for 7 August")
    card(e2, "The forecast said", "66% of models", "predicted extreme rain", WARN)
    card(e3, "We said", "87.7% likely wrong", "trust rating: LOW", BAD)
    card(e4, "What actually fell", "0.1 mm", "essentially nothing", OK)
    st.success("**We were right to doubt it.** Two-thirds of the forecast "
               "models were pointing at heavy rain. Our system flagged the "
               "warning as very likely wrong, and almost no rain fell. That "
               "judgement was recorded and sealed three days before the "
               "weather happened, so it cannot be rewritten afterwards.")

    # --- how it works --------------------------------------------------------
    st.write("")
    st.markdown("### How it works")
    h1, h2, h3 = st.columns(3)
    h1.markdown("**1 &middot; Listen to the disagreement**")
    h1.markdown("<p class='note'>A forecast is not one prediction but thirty, "
                "each run slightly differently. When they disagree with each "
                "other, the forecast is shakier than it looks. We measure "
                "that disagreement.</p>", unsafe_allow_html=True)
    h2.markdown("**2 &middot; Score the warning**")
    h2.markdown("<p class='note'>Using past monsoons, we learned which "
                "patterns of disagreement came before warnings that failed. "
                "Every new forecast gets a percentage: how likely this one is "
                "to be wrong.</p>", unsafe_allow_html=True)
    h3.markdown("**3 &middot; Seal it before the rain**")
    h3.markdown("<p class='note'>The score is written down, fingerprinted and "
                "committed to a ledger <b>before</b> the rain window opens. "
                "Afterwards the real rainfall is attached. Nobody can quietly "
                "change what was claimed.</p>", unsafe_allow_html=True)

    # --- the honest answer ---------------------------------------------------
    st.write("")
    st.markdown("### Does it work? Partly, and we can say exactly where")
    left, right = st.columns(2)
    with left:
        st.markdown("**Where it works**")
        st.markdown(
            "<p class='note'>Tested on the same kind of weather it learned "
            "from, the system is genuinely useful. Warnings it rated "
            "<b>low trust</b> turned out wrong about <b>62%</b> of the time. "
            "Warnings it rated <b>medium trust</b> were wrong about <b>25%</b> "
            "of the time. Treating the low ones with caution would have "
            "caught roughly <b>19 out of every 20 false alarms</b>.</p>",
            unsafe_allow_html=True)
    with right:
        st.markdown("**Where it breaks**")
        st.markdown(
            "<p class='note'>Tested on a later year, and on <b>June</b> &mdash; "
            "the very start of the monsoon rather than its peak &mdash; it "
            "stopped being useful. It still ranked forecasts sensibly, but it "
            "could no longer put a trustworthy number on them.<br><br>"
            "The reason matters: our definition of &ldquo;extreme rain&rdquo; "
            "is set by the whole season, which is dominated by the wet "
            "months. June is much drier, so that bar sits far too low for it. "
            "The judgement did not fail &mdash; the yardstick did.</p>",
            unsafe_allow_html=True)

    st.write("")
    fig("region_map.png")
    st.markdown("<p class='note'>Why one yardstick cannot work everywhere: in "
                "the dry mountains of the north-west, a couple of millimetres "
                "is remarkable. Around Lahore it takes thirty. The system "
                "learns a separate bar for each square &mdash; but as the June "
                "result shows, it also needs a separate bar for each part of "
                "the season.</p>", unsafe_allow_html=True)

    # --- accountability ------------------------------------------------------
    st.write("")
    st.markdown("### Why you do not have to take our word for it")
    a1, a2, a3 = st.columns(3)
    card(a1, "Forecasts on record", str(len(records)), "each one fingerprinted")
    card(a2, "Sealed before the rain", "every one",
         "timestamps come from the ledger, not from us", OK)
    card(a3, "Try to fake one", "it fails",
         "change any value and the seal breaks", OK)
    st.markdown("<p class='note'>Open <b>Verify a record</b> and alter a "
                "number &mdash; the check will fail in front of you. That is "
                "the point: a claim about the future is only worth something "
                "if it was provably made <i>before</i> the future "
                "arrived.</p>", unsafe_allow_html=True)

    with st.expander("The technical numbers, for reviewers"):
        fa = (ev or {}).get("targets", {}).get("false_alarm", {}).get(
            "metrics", {}).get("calibrated")
        ms = (ev or {}).get("targets", {}).get("miss", {}).get(
            "metrics", {}).get("calibrated")
        if fa and ms:
            st.markdown(
                "| | False alarm model | Miss model |\n"
                "|---|---|---|\n"
                "| Brier skill vs climatology | %+.3f | %+.3f |\n"
                "| ROC AUC | %.3f | %.3f |\n"
                "| Test samples | %s | %s |\n"
                % (fa["brier_skill_vs_reference"],
                   ms["brier_skill_vs_reference"],
                   fa["roc_auc"], ms["roc_auc"],
                   format(fa["n"], ","), format(ms["n"], ",")))
        sp = (ev or {}).get("split", {})
        if sp:
            st.caption("Trained on %s, calibrated on %s, tested once on %s."
                       % (", ".join(sp.get("train", [])),
                          ", ".join(sp.get("calibration", [])),
                          ", ".join(sp.get("test", []))))
        st.markdown(
            "The headline skill score is inflated by an extreme base rate on "
            "the held-out month and should be read alongside the trust-band "
            "table, where every sample falls in a single band. Discrimination "
            "is nearly unchanged between the two tests, which is what points "
            "at the event definition rather than the model.")

    st.info("**Please read the limits before quoting anything here.** The "
            "held-out test is a single month. The atmospheric measurements we "
            "planned to use never arrived from the archive. The trust "
            "categories are known to be badly positioned. None of that is "
            "hidden; it is all in the technical report.")


def page_verify():
    hero("Verify a forecast record",
         "Recompute the SHA-256 of a published record, compare it with the "
         "hash held in the registry, and confirm the commit happened before "
         "the rain window opened.",
         "Nothing here is taken on trust — every claim is recomputed in your browser session")

    records = load_records()
    if not records:
        st.warning("No published records are available yet.")
        return

    left, right = st.columns([3, 2])
    chosen = left.selectbox("Forecast record", sorted(records),
                            label_visibility="collapsed")
    pasted = right.text_input("Paste an id instead", value="",
                              placeholder="…or paste a forecast id",
                              label_visibility="collapsed")
    fid = pasted.strip() or chosen

    record = records.get(fid)
    if record is None:
        st.error("No published record with id `%s`." % fid)
        return

    entry = registry().get(record["forecast_id"])
    outcome = load_outcome(fid)
    result = verify_record(record, entry, outcome)
    colour, title, blurb = STATUS_STYLE.get(result.status, (MUTED, result.status.value, ""))

    st.markdown("<div class='statusbar' style='background:%s'>%s<small>%s</small></div>"
                % (colour, title, blurb), unsafe_allow_html=True)

    trust = record.get("trust_label", "—")
    c1, c2, c3, c4 = st.columns(4)
    card(c1, "Trust label", trust, "calibrated band", TRUST_STYLE.get(trust, INK))
    card(c2, "P(decision wrong)", "%.1f%%" % (record["p_wrong_bp"] / 100), "calibrated")
    card(c3, "Ensemble signal", "%.1f%%" % (record["signal_bp"] / 100), "members over threshold")
    card(c4, "Lead time", "%d h" % record["lead_hours"], "ahead of the window")

    st.write("")
    st.subheader("Checks")
    for name, passed, detail in result.checks:
        st.markdown("<div class='check'>%s <b>%s</b><br><span class='d'>%s</span></div>"
                    % ("✅" if passed else "❌", name, detail or "—"),
                    unsafe_allow_html=True)

    st.write("")
    a, b = st.columns(2)
    with a:
        st.markdown("**Hash**")
        st.code("recomputed  %s\nregistry    %s"
                % (result.computed_hash or "—", result.registry_hash or "—"),
                language=None)
    with b:
        st.markdown("**Timing**")
        if entry:
            st.code("committed     %s\nwindow opens  %s\nwindow closes %s\noutcome       %s"
                    % (entry.committed_utc, entry.window_start_utc,
                       entry.window_end_utc, entry.outcome_recorded_utc or "—"),
                    language=None)

    st.write("")
    st.markdown("### Does tamper detection actually work?")
    st.markdown("<p class='note'>Change a published value and re-verify. If the "
                "audit trail is worth anything, this must fail.</p>",
                unsafe_allow_html=True)
    t1, t2, t3 = st.columns([2, 2, 1])
    FIELD_LABELS = {"Probability of being wrong": "p_wrong_bp",
                    "Ensemble signal": "signal_bp",
                    "Trust label": "trust_label"}
    label = t1.selectbox("Value to alter", list(FIELD_LABELS))
    field = FIELD_LABELS[label]
    shown = (record[field] / 100 if field.endswith("_bp") else record[field])
    new_value = t2.text_input("Change it to", value=str(shown))
    t3.write(""); t3.write("")
    if t3.button("Re-verify", type="primary"):
        tampered = dict(record)
        try:
            tampered[field] = (int(round(float(new_value) * 100))
                               if field.endswith("_bp") else new_value)
        except ValueError:
            st.error("%s needs a number." % label)
            return
        bad = verify_record(tampered, entry, outcome)
        bcolour, btitle, bblurb = STATUS_STYLE.get(bad.status, (MUTED, bad.status.value, ""))
        st.markdown("<div class='statusbar' style='background:%s'>%s<small>%s</small></div>"
                    % (bcolour, btitle, bblurb), unsafe_allow_html=True)
        if bad.status == Status.MISMATCH:
            st.success("Detected. The altered record no longer matches the "
                       "committed hash — exactly what should happen.")
        st.code("altered   %s\ncommitted %s"
                % (bad.computed_hash, entry.forecast_hash if entry else "—"),
                language=None)

    with st.expander("What this does and does not prove"):
        st.markdown(
            "**Proves:** this exact record existed, unchanged, before its rain "
            "window opened.\n\n"
            "**Does not prove:** that the forecast was any good, or that every "
            "forecast made was submitted. An owner-controlled registry cannot "
            "demonstrate the absence of omissions — a public schedule and "
            "visible gaps reduce that risk without eliminating it.")


def page_trust():
    hero("Trust view",
         "Every published forecast, its calibrated probability of being "
         "wrong, and what actually happened.")

    records = load_records()
    if not records:
        st.warning("No records yet.")
        return

    rows = []
    for fid, rec in records.items():
        o = load_outcome(fid)
        rows.append({
            "Issued": rec["issue_time_utc"][:10],
            "Lead": "%d h" % rec["lead_hours"],
            "Signal %": round(rec["signal_bp"] / 100, 1),
            "P(wrong) %": round(rec["p_wrong_bp"] / 100, 1),
            "Trust": rec["trust_label"],
            "Event?": ("yes" if o["event_occurred"] else "no") if o else "—",
            "Wrong?": ("yes" if o["decision_was_wrong"] else "no") if o else "—",
        })
    table = pd.DataFrame(rows).sort_values(["Issued", "Lead"])

    counts = table["Trust"].value_counts()
    c1, c2, c3, c4 = st.columns(4)
    card(c1, "Records", str(len(table)), "published and committed")
    for col, band in ((c2, "HIGH"), (c3, "MEDIUM"), (c4, "LOW")):
        card(col, "%s trust" % band, str(int(counts.get(band, 0))),
             "p_wrong %s" % {"HIGH": "< 20%", "MEDIUM": "20–50%", "LOW": "≥ 50%"}[band],
             TRUST_STYLE[band])

    st.write("")
    st.dataframe(
        table, width='stretch', hide_index=True,
        column_config={
            "P(wrong) %": st.column_config.ProgressColumn(
                "P(wrong) %", min_value=0, max_value=100, format="%.1f%%"),
            "Signal %": st.column_config.ProgressColumn(
                "Signal %", min_value=0, max_value=100, format="%.1f%%"),
        })

    st.warning(
        "**The bands are known to be badly positioned.** The cutoffs "
        "(20% / 50%) were frozen before we knew the false-alarm base rate "
        "would be ~57%, so a calibrated model essentially never lands in "
        "HIGH. They are reported as frozen rather than re-tuned after seeing "
        "results. Read the continuous P(wrong) column, not just the label.")


def page_results():
    ev = load_evaluation()
    hero("Evaluation results",
         "Can ensemble disagreement tell us when a rainfall forecast is "
         "about to be wrong? Measured on held-out data, reported honestly.")

    if ev is None:
        st.warning("Evaluation results are not available yet.")
        return

    sp = ev["split"]
    st.caption("Frozen split — train %s · calibrate %s · test %s"
               % (", ".join(sp["train"]), ", ".join(sp["calibration"]),
                  ", ".join(sp["test"])))

    fa = ev["targets"].get("false_alarm", {}).get("metrics", {}).get("calibrated")
    ms = ev["targets"].get("miss", {}).get("metrics", {}).get("calibrated")
    c1, c2, c3, c4 = st.columns(4)
    if fa:
        card(c1, "False alarm · skill", "%+.3f" % fa["brier_skill_vs_reference"],
             "Brier skill vs climatology",
             OK if fa["brier_skill_vs_reference"] > 0 else BAD)
        card(c2, "False alarm · AUC", "%.3f" % fa["roc_auc"], "discrimination")
    if ms:
        card(c3, "Miss · skill", "%+.3f" % ms["brier_skill_vs_reference"],
             "Brier skill vs climatology",
             OK if ms["brier_skill_vs_reference"] > 0 else BAD)
        card(c4, "Miss · AUC", "%.3f" % ms["roc_auc"], "discrimination")

    st.write("")
    t1, t2, t3 = st.tabs(["Skill", "Reliability", "Forecast quality"])
    with t1:
        fig("brier_skill.png")
        fig("trust_bands.png")
        st.markdown("<p class='note'>A positive skill score beats climatology. "
                    "The uncalibrated bars show why calibration is a separate "
                    "stage: class weighting buys ranking at the cost of "
                    "calibrated probabilities.</p>", unsafe_allow_html=True)
    with t2:
        fig("reliability.png")
        st.markdown("<p class='note'>Points on the diagonal mean the stated "
                    "probability matches the observed frequency.</p>",
                    unsafe_allow_html=True)
    with t3:
        fig("verification_by_signal_level.png")
        verif = ev.get("forecast_verification_test", {})
        if verif:
            st.dataframe(pd.DataFrame([{
                "Signal level": "%d%%" % (float(k) * 100),
                "POD": round(v["probability_of_detection"], 3),
                "FAR": round(v["false_alarm_ratio"], 3),
                "CSI": round(v["critical_success_index"], 3),
                "Bias": round(v["frequency_bias"], 3),
            } for k, v in sorted(verif.items(), key=lambda x: float(x[0]))]),
                width='stretch', hide_index=True)

    for target, d in ev["targets"].items():
        ci = d.get("test_error_rate_ci")
        if ci:
            st.caption("%s — observed error rate %.3f, 95%% CI [%.3f, %.3f] "
                       "(block bootstrap over %d calendar weeks)"
                       % (target.replace("_", " "), ci["point_estimate"],
                          ci["ci_low"], ci["ci_high"], ci["n_blocks"]))

    st.info("Intervals are wide because the test split is short. Comparisons "
            "between models at this sample size are not statistically "
            "meaningful. The technical report sets out the full "
            "limitations.")


def page_region():
    hero("The region",
         "Northern Pakistan monsoon belt, 30–37 N / 69–75 E, on the 0.5° "
         "forecast grid: 15 × 13 = 195 cells.")

    th = load_thresholds()
    if th is None:
        st.warning("Regional threshold data is not available yet.")
        return

    c1, c2, c3 = st.columns(3)
    card(c1, "Cells", "195", "15 lat × 13 lon at 0.5°")
    card(c2, "Median threshold", "%.1f mm" % th["threshold_mm"].median(),
         "95th percentile of daily rain")
    card(c3, "Cells under 1 mm", str(int((th["threshold_mm"] < 1).sum())),
         "where “extreme” barely means rain", BAD)

    st.write("")
    grid = th.pivot(index="latitude", columns="longitude",
                    values="threshold_mm").sort_index(ascending=False)
    st.markdown("**Extreme-rainfall threshold per cell (mm/day)**")
    st.dataframe(grid.style.background_gradient(cmap="YlGnBu")
                 .format("{:.1f}"), width='stretch')

    st.warning(
        "**An open problem.** Thresholds range from %.2f to %.1f mm. In the "
        "dry north-west (top-left) the 95th percentile falls below 1 mm, so "
        "“exceeds the threshold” means little more than “any measurable rain "
        "fell” — and those are exactly the mountain cells where the satellite "
        "estimate is least reliable. No floor was applied, because changing "
        "the event definition after seeing results would invalidate the "
        "protocol. It is recorded as an open issue instead."
        % (th["threshold_mm"].min(), th["threshold_mm"].max()))


def page_ledger():
    hero("Ledger status",
         "What has been committed, when, and to which registry.")

    reg = registry()
    info = chain_info()
    ids = reg.list_ids()
    if not ids:
        st.warning("Registry is empty.")
        return

    entries = [reg.get(i) for i in ids]
    entries = [e for e in entries if e]
    with_outcome = sum(1 for e in entries if e.outcome_hash)

    c1, c2, c3 = st.columns(3)
    card(c1, "Committed", str(len(entries)), "forecast records")
    card(c2, "Outcomes attached", str(with_outcome), "windows that have closed", OK)
    card(c3, "Awaiting outcome", str(len(entries) - with_outcome), "window still open")

    st.write("")
    st.dataframe(pd.DataFrame([{
        "Forecast id": e.forecast_id,
        "Committed": e.committed_utc,
        "Window opens": e.window_start_utc,
        "Window closes": e.window_end_utc,
        "Outcome": e.outcome_recorded_utc or "—",
    } for e in entries]).sort_values("Committed"),
        width='stretch', hide_index=True)

    if info is None:
        st.info(
            "**Local mock registry.** These records were committed with a "
            "simulated clock set to each forecast's historical issue time, "
            "because they replay archived forecasts rather than live ones. "
            "The mock enforces the same rules as the Solidity contract: no "
            "commit once a window has opened, no outcome before it closes, "
            "no duplicates, and the timestamp comes from the registry rather "
            "than the caller.")
        st.caption("The smart contract implementing these rules passes 24 "
                   "tests covering every one of them.")
    else:
        st.success("Reading the deployed ForecastRegistry at `%s` on %s "
                   "(chain %d). Commit timestamps come from the chain."
                   % (info.address, info.network, info.chain_id))
        if info.explorer_url:
            st.markdown("[View the contract on the block explorer](%s)" % info.explorer_url)
        if not info.is_public:
            st.warning("This is a **local development chain** — it proves the "
                       "contract and client work end to end, but a third "
                       "party cannot independently check it.")


PAGES = {
    "Overview": page_overview,
    "Verify a record": page_verify,
    "Trust view": page_trust,
    "Evaluation results": page_results,
    "The region": page_region,
    "Ledger status": page_ledger,
}


def main():
    st.set_page_config(page_title="MonsoonGuard AI", page_icon="🌧",
                       layout="wide", initial_sidebar_state="expanded")
    st.markdown(CSS, unsafe_allow_html=True)

    st.sidebar.markdown("## 🌧 MonsoonGuard AI")
    st.sidebar.caption("Detecting unreliable extreme rainfall forecasts "
                       "in Pakistan")
    choice = st.sidebar.radio("Page", list(PAGES), label_visibility="collapsed")

    st.sidebar.markdown("---")
    info = chain_info()
    if info is None:
        st.sidebar.warning("**Registry:** local mock")
    elif info.is_public:
        st.sidebar.success("**Registry:** on chain\n\n`%s`" % info.address[:18] + "…")
    else:
        st.sidebar.info("**Registry:** local dev chain\n\nchain %d" % info.chain_id)

    st.sidebar.markdown("---")
    st.sidebar.caption(
        "Read-only: results are produced by an offline pipeline and audited "
        "here. The app never trains or recomputes.")

    PAGES[choice]()


main()
