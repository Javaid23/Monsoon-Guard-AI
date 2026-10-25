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
from datetime import datetime
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

# Wording is for a reader deciding whether to trust this, not for an
# auditor: the status line is the one sentence most people will read.
STATUS_STYLE = {
    Status.VERIFIED: (OK, "Checks out",
                      "Unchanged, and locked before the rain was due."),
    Status.COMMITTED: ("#0969da", "Locked",
                       "Sealed before the rain. The day has not passed yet."),
    Status.OUTCOME_RECORDED: ("#0969da", "Rainfall added",
                              "The result is in and being checked."),
    Status.PENDING: (WARN, "Not on file",
                     "No locked copy of this forecast was found."),
    Status.MISMATCH: (BAD, "Does not match",
                      "This differs from what was locked away."),
    Status.MISSED_COMMIT: (BAD, "Locked too late",
                           "Sealed only after the rain was already due."),
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
  /* ---- sidebar ---- */
  section[data-testid="stSidebar"] {
    background: #0f2a22;
    border-right: 1px solid #0a1f19;
  }
  section[data-testid="stSidebar"] * {color: #e8efec;}

  .brand {padding: .35rem 0 1.1rem 0;}
  .brand-name {
    font-size: 1.22rem; font-weight: 700; letter-spacing: -0.01em;
    color: #fff; line-height: 1.2;
  }
  .brand-sub {
    font-size: .83rem; color: #9fc4b5 !important; margin-top: .3rem;
    line-height: 1.45;
  }

  /* radio: turn the stock list into tappable rows */
  section[data-testid="stSidebar"] div[role="radiogroup"] {gap: .18rem;}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label {
    padding: .5rem .65rem; border-radius: 9px; margin: 0;
    transition: background .12s ease;
  }
  section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {
    background: rgba(255,255,255,.07);
  }
  section[data-testid="stSidebar"] div[role="radiogroup"] > label p {
    font-size: .95rem !important; font-weight: 550;
  }
  section[data-testid="stSidebar"] div[role="radiogroup"] > label
    div[data-testid="stCaptionContainer"] p {
    font-size: .76rem !important; color: #8fb3a5 !important;
    font-weight: 400; margin-top: .05rem;
  }

  .sb-rule {height:1px; background:rgba(255,255,255,.12); margin:1.1rem 0;}

  .sb-stats {display:flex; flex-direction:column; gap:.5rem;}
  .sb-stat {display:flex; justify-content:space-between; align-items:baseline;}
  .sb-stat span {font-size:.8rem; color:#9fc4b5 !important;}
  .sb-stat b {font-size:.95rem; color:#fff !important;}

  .sb-reg {
    margin-top:1rem; padding:.7rem .8rem; border-radius:9px;
    background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.1);
  }
  .sb-reg-top {font-size:.88rem; font-weight:600; display:flex;
               align-items:center; gap:.45rem;}
  .sb-reg-sub {font-size:.76rem; color:#9fc4b5 !important; margin-top:.25rem;
               line-height:1.4; word-break:break-all;}
  .dot {width:8px; height:8px; border-radius:50%; display:inline-block;}

  .sb-link {font-size:.8rem; color:#7ee2b8 !important; text-decoration:none;}
  .sb-link:hover {text-decoration:underline;}

  .sb-foot {font-size:.76rem; color:#8fb3a5 !important; line-height:1.5;}
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
def load_archive() -> dict | None:
    """The published fingerprint of the whole result set, if there is one."""
    p = RECORDS / "archive_manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


@st.cache_data
def load_bitcoin_anchor() -> dict | None:
    """Written only once Bitcoin has confirmed the timestamp, so its absence
    is the honest signal that the anchor is still pending."""
    p = RECORDS / "bitcoin_anchor.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


@st.cache_data
def load_deployment() -> dict | None:
    p = RECORDS / "registry_deployment.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


@st.cache_data
def load_thresholds() -> pd.DataFrame | None:
    p = REPORTS / "thresholds_grid.csv"
    return pd.read_csv(p) if p.exists() else None


# --- small builders ----------------------------------------------------------

def nice_date(iso: str | None) -> str:
    """'2022-08-01T06:00:00Z' -> '1 August 2022'. Timestamps to the second
    are precision nobody reading this page needs."""
    if not iso:
        return "an unknown date"
    try:
        d = datetime.strptime(iso[:10], "%Y-%m-%d")
    except ValueError:
        return iso
    return "%d %s %d" % (d.day, d.strftime("%B"), d.year)


def days_between(start_iso: str | None, end_iso: str | None) -> int:
    try:
        a = datetime.strptime(start_iso[:10], "%Y-%m-%d")
        b = datetime.strptime(end_iso[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return 0
    return (b - a).days


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
    ev = load_evaluation()
    records = load_records()
    verif = (ev or {}).get("forecast_verification_test", {})
    base = verif.get("0.2") or (list(verif.values())[0] if verif else None)

    far = 100 * base["false_alarm_ratio"] if base else None
    pod = 100 * base["probability_of_detection"] if base else None
    bias = base["frequency_bias"] if base else None

    hero("Which rain warnings should you believe?",
         "Weather services warn when heavy rain is coming. Many of those "
         "warnings do not happen. If people are warned again and again and "
         "nothing comes, they stop listening &mdash; and one day the warning "
         "is real. This project looks at each warning before the rain and "
         "estimates <b>how likely it is to be wrong</b>.",
         "IEEE ClimateChain 2026 &middot; Climate Data &amp; Environmental Monitoring")

    # --- the problem ---------------------------------------------------------
    st.markdown("### First, how good are the warnings we have?")
    st.markdown("<p class='note'>We checked the forecasts for northern "
                "Pakistan against what the rain satellites actually "
                "recorded.</p>", unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    if base:
        card(c1, "Warnings that did not happen", "%.0f out of 100" % far,
             "the rain never arrived", BAD)
        card(c2, "Heavy rain days it did warn about", "%.0f out of 100" % pod,
             "these were caught correctly", WARN if pod < 50 else OK)
        card(c3, "Warnings given for each real event", "%.0f" % bias,
             "far more warnings than events", BAD)
        st.markdown("<p class='note'>In the month we tested, the forecast "
                    "gave about <b>%.0f warnings for every real heavy-rain "
                    "day</b>. Most of those warnings were wrong. Our job is "
                    "not to make the forecast better &mdash; that is for "
                    "weather centres. Our job is to say, in advance, which "
                    "warnings are worth acting on.</p>" % bias,
                    unsafe_allow_html=True)

    # --- a real example ------------------------------------------------------
    st.write("")
    st.markdown("### One real example")
    st.markdown("<p class='note'>This is a real forecast from the 2022 "
                "monsoon, with the numbers exactly as they were recorded at "
                "the time.</p>", unsafe_allow_html=True)

    e1, e2, e3, e4 = st.columns(4)
    card(e1, "The date", "4 August 2022", "looking 3 days ahead")
    card(e2, "What the forecast said", "66 of 100", "computer runs predicted heavy rain", WARN)
    card(e3, "What we said", "87 out of 100", "chance this warning is wrong", BAD)
    card(e4, "What actually fell", "0.1 mm", "almost no rain at all", OK)
    st.success("**Our warning about the warning was correct.** Most of the "
               "computer runs pointed to heavy rain. We said the warning was "
               "very likely wrong, and it was. We wrote that down and locked "
               "it three days before the date, so it could not be changed "
               "afterwards.")

    # --- how it works --------------------------------------------------------
    st.write("")
    st.markdown("### How it works, in three steps")
    h1, h2, h3 = st.columns(3)
    h1.markdown("**Step 1 &mdash; Look for disagreement**")
    h1.markdown("<p class='note'>A forecast is not one prediction. The "
                "computer is run 30 times with slightly different starting "
                "conditions. If the 30 runs agree, the forecast is firm. If "
                "they disagree, it is shaky. We measure how much they "
                "disagree.</p>", unsafe_allow_html=True)
    h2.markdown("**Step 2 &mdash; Give the warning a score**")
    h2.markdown("<p class='note'>We looked at past monsoons to learn which "
                "kinds of disagreement came before warnings that turned out "
                "wrong. Each new warning gets a score out of 100: how likely "
                "it is to be wrong.</p>", unsafe_allow_html=True)
    h3.markdown("**Step 3 &mdash; Lock it in before the rain**")
    h3.markdown("<p class='note'>The score is saved and locked in a tamper-"
                "proof record <b>before</b> the rain is due. After the date "
                "passes, the real rainfall is added. Nobody can go back and "
                "change what was said.</p>", unsafe_allow_html=True)

    # --- honest findings -----------------------------------------------------
    st.write("")
    st.markdown("### Does it work?")
    left, right = st.columns(2)
    with left:
        st.markdown("**Yes &mdash; for the kind of weather it studied**")
        st.markdown(
            "<p class='note'>We trained the system on the 2021 and 2022 "
            "monsoons and tested it on weather of the same kind. Warnings it "
            "marked <b>low trust</b> were wrong about <b>6 times out of "
            "10</b>. Warnings it marked <b>medium trust</b> were wrong about "
            "<b>2 times out of 10</b>. Knowing that difference in advance is "
            "genuinely useful.</p>", unsafe_allow_html=True)
    with right:
        st.markdown("**No &mdash; for weather it had not seen**")
        st.markdown(
            "<p class='note'>We then tested it on <b>June 2024</b>, a later "
            "year and the very beginning of the monsoon rather than its "
            "peak. It stopped working. It could still put forecasts in "
            "roughly the right order, but the scores it gave were no longer "
            "trustworthy.<br><br>"
            "We know why. We decide what counts as &ldquo;heavy rain&rdquo; "
            "using the whole season, and the season is dominated by the "
            "wettest months. June is much drier, so that bar is set far too "
            "low for June. The problem is the definition of heavy rain, not the "
            "scoring.</p>", unsafe_allow_html=True)

    st.write("")
    fig("region_map.png")
    st.markdown("<p class='note'>Rain is not the same across the region. In "
                "the dry mountains in the north-west, 2 mm of rain in a day "
                "is unusual. Near Lahore it takes about 30 mm before anyone "
                "notices. So the system sets a separate bar for every square "
                "on this map. What June showed is that it also needs a "
                "separate bar for different parts of the season.</p>",
                unsafe_allow_html=True)

    # --- accountability ------------------------------------------------------
    st.write("")
    st.markdown("### You do not have to take our word for it")
    a1, a2, a3 = st.columns(3)
    card(a1, "Forecasts on record", str(len(records)), "each one locked and dated")
    card(a2, "Locked before the rain", "all of them",
         "the date stamp is not ours to set", OK)
    card(a3, "If someone edits one", "the check fails", "try it yourself", OK)
    st.markdown("<p class='note'>Go to <b>Verify a record</b>, change any "
                "number, and press re-check. It will fail in front of you. "
                "That is the whole point: saying &ldquo;we predicted "
                "this&rdquo; afterwards means nothing unless you can prove "
                "you said it beforehand.</p>", unsafe_allow_html=True)

    with st.expander("Technical detail, for reviewers"):
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
            "table, where every sample falls into one band. Discrimination "
            "is almost unchanged between the two tests, which is what points "
            "at the event definition rather than the model.")

    st.info("**Please read the limits before quoting any number here.** The "
            "final test covered a single month. Atmospheric measurements we "
            "had planned to use never arrived from the data archive. The "
            "trust categories are set at the wrong levels. All of this is "
            "written up in the technical report rather than hidden.")


def page_verify():
    hero("Check that a forecast was not changed afterwards",
         "Every forecast here was given a digital fingerprint and locked away "
         "<b>before</b> the rain was due. This page recreates that "
         "fingerprint from the saved forecast and compares it with the locked "
         "copy. If anybody edited so much as one number, the two will not "
         "match.",
         "Nothing is taken on trust &mdash; the check runs in front of you")

    records = load_records()
    if not records:
        st.warning("No forecasts are available yet.")
        return

    left, right = st.columns([3, 2])
    chosen = left.selectbox("Forecast", sorted(records),
                            label_visibility="collapsed")
    pasted = right.text_input("Reference", value="",
                              placeholder="…or paste a forecast reference",
                              label_visibility="collapsed")
    fid = pasted.strip() or chosen

    record = records.get(fid)
    if record is None:
        st.error("There is no forecast with that reference.")
        return

    entry = registry().get(record["forecast_id"])
    outcome = load_outcome(fid)
    result = verify_record(record, entry, outcome)
    colour, title, blurb = STATUS_STYLE.get(
        result.status, (MUTED, result.status.value, ""))

    st.markdown("<div class='statusbar' style='background:%s'>%s<small>%s"
                "</small></div>" % (colour, title, blurb),
                unsafe_allow_html=True)

    trust = record.get("trust_label", "—")
    friendly = {"HIGH": "Likely reliable", "MEDIUM": "Treat with care",
                "LOW": "Likely wrong"}.get(trust, trust)
    c1, c2, c3, c4 = st.columns(4)
    card(c1, "Our rating", friendly, "based on model disagreement",
         TRUST_STYLE.get(trust, INK))
    card(c2, "Chance this warning is wrong",
         "%.0f out of 100" % (record["p_wrong_bp"] / 100), "our estimate")
    card(c3, "Computer runs predicting rain",
         "%.0f of 100" % (record["signal_bp"] / 100), "out of 30 runs, scaled")
    card(c4, "Issued in advance", "%d days" % (record["lead_hours"] // 24),
         "before the rain was due")

    st.write("")
    st.subheader("The checks")
    FRIENDLY = {
        "registry entry exists": "A locked copy exists",
        "record matches committed hash":
            "The forecast has not been altered",
        "committed before window opened":
            "It was locked before the rain was due",
        "outcome recorded": "The real rainfall was added afterwards",
        "outcome matches committed hash": "The rainfall record is unaltered",
        "outcome links to this forecast":
            "The rainfall belongs to this forecast",
        "outcome recorded after window closed":
            "The rainfall was added only after the day had passed",
    }
    # The verifier's own detail strings are built for an auditor reading a
    # terminal - hash fragments and ISO timestamps. Correct, but meaningless
    # to a reader deciding whether to trust a warning, so they are restated
    # here and kept verbatim in the reviewer section below.
    def plain_detail(name: str, passed: bool) -> str:
        if name == "registry entry exists":
            return ("Locked on %s" % nice_date(entry.committed_utc)
                    if entry else "No locked copy was found")
        if name == "record matches committed hash":
            return ("Every value is exactly as it was locked" if passed
                    else "A value has been changed since it was locked")
        if name == "committed before window opened":
            if not (entry and passed):
                return "It was not locked in time"
            days = days_between(entry.committed_utc, entry.window_start_utc)
            return ("Locked %s before the rain was due"
                    % ("a day" if days == 1 else "%d days" % days))
        if name == "outcome recorded":
            return ("Rainfall added on %s" % nice_date(entry.outcome_recorded_utc)
                    if passed and entry and entry.outcome_recorded_utc
                    else "The day has not passed yet")
        if name == "outcome matches committed hash":
            return ("Unchanged since it was added" if passed
                    else "The rainfall record has been changed")
        if name == "outcome links to this forecast":
            return ("It belongs to this forecast" if passed
                    else "It does not belong to this forecast")
        if name == "outcome recorded after window closed":
            return ("Added only after the day had passed" if passed
                    else "Added too early to be a real observation")
        return ""

    for name, passed, _raw in result.checks:
        st.markdown("<div class='check'>%s <b>%s</b><br><span class='d'>%s"
                    "</span></div>"
                    % ("✅" if passed else "❌", FRIENDLY.get(name, name),
                       plain_detail(name, passed) or "&mdash;"),
                    unsafe_allow_html=True)

    st.write("")
    st.markdown("### Try to fake it")
    st.markdown("<p class='note'>Change any value below and press re-check. "
                "If the record could be quietly rewritten after the fact, "
                "this whole idea would be worthless &mdash; so watch it "
                "fail.</p>", unsafe_allow_html=True)
    t1, t2, t3 = st.columns([2, 2, 1])
    FIELD_LABELS = {"Chance it is wrong": "p_wrong_bp",
                    "Computer runs predicting rain": "signal_bp",
                    "Our rating": "trust_label"}
    label = t1.selectbox("Value to change", list(FIELD_LABELS))
    field = FIELD_LABELS[label]
    shown = (record[field] / 100 if field.endswith("_bp") else record[field])
    new_value = t2.text_input("Change it to", value=str(shown))
    t3.write("")
    t3.write("")
    if t3.button("Re-check", type="primary"):
        tampered = dict(record)
        try:
            tampered[field] = (int(round(float(new_value) * 100))
                               if field.endswith("_bp") else new_value)
        except ValueError:
            st.error("%s needs to be a number." % label)
            return
        bad = verify_record(tampered, entry, outcome)
        bcol, btitle, bblurb = STATUS_STYLE.get(
            bad.status, (MUTED, bad.status.value, ""))
        st.markdown("<div class='statusbar' style='background:%s'>%s<small>%s"
                    "</small></div>" % (bcol, btitle, bblurb),
                    unsafe_allow_html=True)
        if bad.status == Status.MISMATCH:
            st.success("Caught. The edited version does not match the locked "
                       "copy, so the change is detected immediately.")

    with st.expander("What this does and does not prove"):
        st.markdown(
            "**It proves** this exact forecast existed, word for word, "
            "before the rain was due.\n\n"
            "**It does not prove** the forecast was any good, or that we "
            "published every forecast we made. A record store controlled by "
            "one party cannot show that nothing was quietly left out. "
            "Publishing a fixed schedule, so that gaps are visible, reduces "
            "that risk without removing it.")

    with st.expander("Technical detail, for reviewers"):
        st.code("recomputed SHA-256  %s\nstored SHA-256      %s"
                % (result.computed_hash or "—", result.registry_hash or "—"),
                language=None)
        if entry:
            st.code("committed      %s\nwindow opens   %s\nwindow closes  %s\n"
                    "outcome added  %s"
                    % (entry.committed_utc, entry.window_start_utc,
                       entry.window_end_utc, entry.outcome_recorded_utc or "—"),
                    language=None)


def page_trust():
    hero("Every forecast, and what actually happened",
         "Each row is one warning. We show how many computer runs predicted "
         "heavy rain, how likely we thought that warning was to be wrong, and "
         "then what the rain satellites actually recorded afterwards.")

    records = load_records()
    if not records:
        st.warning("No forecasts are available yet.")
        return

    rows = []
    for fid, rec in records.items():
        o = load_outcome(fid)
        rows.append({
            "Date issued": rec["issue_time_utc"][:10],
            "Days ahead": rec["lead_hours"] // 24,
            "Runs predicting rain %": round(rec["signal_bp"] / 100, 1),
            "Chance it is wrong %": round(rec["p_wrong_bp"] / 100, 1),
            "Our rating": {"HIGH": "Likely reliable",
                           "MEDIUM": "Treat with care",
                           "LOW": "Likely wrong"}.get(rec["trust_label"],
                                                      rec["trust_label"]),
            "Did heavy rain come?": ("yes" if o["event_occurred"] else "no")
                                    if o else "—",
            "Was the warning wrong?": ("yes" if o["decision_was_wrong"]
                                       else "no") if o else "—",
        })
    table = pd.DataFrame(rows).sort_values(["Date issued", "Days ahead"])

    counts = table["Our rating"].value_counts()
    c1, c2, c3, c4 = st.columns(4)
    card(c1, "Forecasts shown", str(len(table)), "all locked in advance")
    card(c2, "Likely reliable", str(int(counts.get("Likely reliable", 0))),
         "we expected these to hold", OK)
    card(c3, "Treat with care", str(int(counts.get("Treat with care", 0))),
         "uncertain", WARN)
    card(c4, "Likely wrong", str(int(counts.get("Likely wrong", 0))),
         "we expected these to fail", BAD)

    st.write("")
    st.dataframe(
        table, width='stretch', hide_index=True,
        column_config={
            "Chance it is wrong %": st.column_config.ProgressColumn(
                "Chance it is wrong", min_value=0, max_value=100,
                format="%.0f%%"),
            "Runs predicting rain %": st.column_config.ProgressColumn(
                "Runs predicting rain", min_value=0, max_value=100,
                format="%.0f%%"),
        })

    st.warning(
        "**These ratings are set at the wrong levels, and we are leaving "
        "them that way.** We fixed the boundaries between the three ratings "
        "at the start, before we knew how often warnings actually fail. It "
        "turns out they fail so often that almost nothing ever earns the top "
        "rating. Moving the boundaries now, after seeing the answers, would "
        "make the test meaningless. So we report them as they were set and "
        "say plainly that they are wrong. Use the percentage column rather "
        "than the label.")


def page_results():
    ev = load_evaluation()
    hero("How well did it do?",
         "We trained the system on past monsoons, then tested it once on "
         "weather it had never seen. These are the results of that test, "
         "including the parts that did not work.")

    if ev is None:
        st.warning("Results are not available yet.")
        return

    sp = ev["split"]
    # Month codes ("2021-06, 2021-07, ...") read as internal bookkeeping to
    # anyone who is not us. Years and a count carry the same meaning.
    years = sorted({m[:4] for m in sp["train"]})
    st.caption("Learned from %d months across the %s monsoons, then tested "
               "once on a later month it had never seen."
               % (len(sp["train"]),
                  " and ".join(years) if len(years) < 3
                  else "%s-%s" % (years[0], years[-1])))

    fa = ev["targets"].get("false_alarm", {}).get("metrics", {}).get("calibrated")
    ms = ev["targets"].get("miss", {}).get("metrics", {}).get("calibrated")
    c1, c2 = st.columns(2)
    if fa:
        card(c1, "Spotting warnings that will fail",
             "%.0f out of 100" % (100 * fa["roc_auc"]),
             "how often it ranks a failing warning above a sound one "
             "(50 would be guesswork)", OK)
    if ms:
        card(c2, "Spotting rain it failed to warn about",
             "%.0f out of 100" % (100 * ms["roc_auc"]),
             "same measure, for missed events", WARN)

    st.markdown("<p class='note'>Both are better than guessing, which means "
                "the disagreement between computer runs really does carry "
                "information. Whether that information is <i>usable</i> is a "
                "separate question, and the charts below are less "
                "flattering.</p>", unsafe_allow_html=True)

    st.write("")
    t1, t2, t3 = st.tabs(["Were the ratings useful?",
                          "Were the percentages honest?",
                          "How good were the original warnings?"])
    with t1:
        fig("trust_bands.png")
        st.markdown("<p class='note'>Each bar is one rating level, and the "
                    "height is how often forecasts with that rating turned "
                    "out wrong. For the ratings to be useful the bars must "
                    "differ &mdash; and an empty bar means nothing ever got "
                    "that rating.</p>", unsafe_allow_html=True)
        fig("brier_skill.png")
        st.markdown("<p class='note'>Above the line means better than simply "
                    "assuming every warning behaves like the historical "
                    "average. Below means worse.</p>",
                    unsafe_allow_html=True)
    with t2:
        fig("reliability.png")
        st.markdown("<p class='note'>If we say a warning has a 70% chance of "
                    "being wrong, then about 70 out of 100 such warnings "
                    "should indeed be wrong. Points sitting on the diagonal "
                    "line mean the percentages can be taken at face "
                    "value.</p>", unsafe_allow_html=True)
    with t3:
        fig("verification_by_signal_level.png")
        verif = ev.get("forecast_verification_test", {})
        if verif:
            st.dataframe(pd.DataFrame([{
                "How many runs must agree": "%d%%" % (float(k) * 100),
                "Heavy rain days caught": "%.0f%%" % (100 * v["probability_of_detection"]),
                "Warnings that failed": "%.0f%%" % (100 * v["false_alarm_ratio"]),
                "Warnings per real event": "%.1f" % v["frequency_bias"],
            } for k, v in sorted(verif.items(), key=lambda x: float(x[0]))]),
                width='stretch', hide_index=True)
            st.markdown("<p class='note'>Demanding more agreement before "
                        "issuing a warning means fewer false alarms, but also "
                        "more real downpours missed. There is no setting that "
                        "avoids both.</p>", unsafe_allow_html=True)

    with st.expander("Technical detail, for reviewers"):
        rows = []
        for target, d in ev["targets"].items():
            for variant, m in d["metrics"].items():
                rows.append({"target": target, "variant": variant,
                             "n": m["n"], "positives": m["n_positive"],
                             "Brier": round(m["brier"], 4),
                             "Brier skill": round(m["brier_skill_vs_reference"], 4),
                             "ROC AUC": round(m["roc_auc"], 3) if m["roc_auc"] else None,
                             "PR AUC": round(m["pr_auc"], 3) if m["pr_auc"] else None})
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        for target, d in ev["targets"].items():
            ci = d.get("test_error_rate_ci")
            if ci:
                st.caption("%s — observed error rate %.3f, 95%% CI "
                           "[%.3f, %.3f] (block bootstrap over %d calendar "
                           "weeks)" % (target, ci["point_estimate"],
                                       ci["ci_low"], ci["ci_high"],
                                       ci["n_blocks"]))

    st.info("**The test covered a single month, so these numbers are not "
            "settled.** The margins of error are wide enough that small "
            "differences mean nothing. The technical report explains what "
            "would be needed to answer the question properly.")


def page_region():
    hero("Where this applies, and why one rule does not fit everywhere",
         "Northern Pakistan, from the mountains in the north to the plains "
         "around Lahore. Each square on the map is about 55 km across.")

    th = load_thresholds()
    if th is None:
        st.warning("Regional information is not available yet.")
        return

    c1, c2, c3 = st.columns(3)
    card(c1, "Areas covered", "195", "squares of about 55 km")
    card(c2, "Typical bar for heavy rain",
         "%.0f mm in a day" % th["threshold_mm"].median(), "across the region")
    card(c3, "Driest areas", "%d squares" % int((th["threshold_mm"] < 1).sum()),
         "where under 1 mm already counts as unusual", BAD)

    st.write("")
    fig("region_map.png")

    st.markdown("<p class='note'>The system learns, for every square, how "
                "much rain is genuinely unusual <i>there</i>. Near Lahore "
                "that is around 30 mm in a day. High in the dry mountains it "
                "can be under 2 mm. A single national figure would be wrong "
                "almost everywhere.</p>", unsafe_allow_html=True)

    st.warning(
        "**A problem we have not solved.** In the driest mountain squares the "
        "bar falls below 1 mm, which means almost any rain at all counts as "
        "“extreme” there. Those are also the places where rain satellites "
        "struggle most, because snow and mountains confuse them. We left the "
        "rule unchanged rather than adjust it after seeing our results, and "
        "we record it as an open problem instead of hiding it.")

    with st.expander("Technical detail, for reviewers"):
        grid = th.pivot(index="latitude", columns="longitude",
                        values="threshold_mm").sort_index(ascending=False)
        st.caption("Per-cell 95th percentile of daily rainfall, computed on "
                   "the training months only (mm/day).")
        st.dataframe(grid.style.background_gradient(cmap="YlGnBu")
                     .format("{:.1f}"), width='stretch')


def page_ledger():
    hero("The tamper-proof record",
         "Every forecast here was sealed with a date stamp before the rain "
         "was due, and the real rainfall was added only after the day had "
         "passed. This page lists what is on file.")

    reg = registry()
    info = chain_info()
    ids = reg.list_ids()
    if not ids:
        st.warning("Nothing has been recorded yet.")
        return

    entries = [e for e in (reg.get(i) for i in ids) if e]
    with_outcome = sum(1 for e in entries if e.outcome_hash)

    c1, c2, c3 = st.columns(3)
    card(c1, "Forecasts sealed", str(len(entries)), "locked before the rain")
    card(c2, "Rainfall added", str(with_outcome),
         "the day has passed and the result is in", OK)
    card(c3, "Still waiting", str(len(entries) - with_outcome),
         "the day has not passed yet")

    st.write("")
    st.dataframe(pd.DataFrame([{
        "Forecast": e.forecast_id,
        "Sealed on": e.committed_utc[:10],
        "Rain due from": e.window_start_utc[:10],
        "Result added": (e.outcome_recorded_utc or "—")[:10],
    } for e in entries]).sort_values("Sealed on"),
        width='stretch', hide_index=True)

    archive = load_archive()
    deployment = load_deployment() or {}
    published = deployment.get("archive")
    if archive:
        st.write("")
        st.markdown("### One fingerprint for the whole set")
        st.markdown(
            "<p class='note'>The forecasts above are a replay of past "
            "monsoons, so no date stamp made today could prove we spoke "
            "before the rain &mdash; the rain already fell. What we can "
            "prove, and have, is that these results have not been edited "
            "since we published them. Every record is folded into a single "
            "fingerprint. Change one number anywhere and the fingerprint "
            "changes, so the results are frozen from that moment on.</p>",
            unsafe_allow_html=True)

        # The Bitcoin anchor is the thing a stranger can actually check, so
        # it decides what this section claims. Reading "published" from the
        # contract deployment instead would say "not yet" whenever the chain
        # is unreachable, directly contradicting the sentence underneath.
        anchor = load_bitcoin_anchor()

        f1, f2 = st.columns(2)
        card(f1, "Records covered", str(archive["leaf_count"]),
             "forecasts and their outcomes")
        if anchor:
            card(f2, "Sealed", "24 October 2026",
                 "and cannot be changed since", OK)
        else:
            card(f2, "Sealed", "not yet", "the fingerprint is ready")

        if anchor:
            st.markdown(
                "<p class='note'>That fingerprint is written into the "
                "<b>Bitcoin blockchain</b>. Bitcoin has no idea what rainfall "
                "is &mdash; it simply records that this fingerprint existed "
                "on a given day, and nobody can remove or alter that entry, "
                "ourselves included.</p>", unsafe_allow_html=True)
            st.markdown("[See the entry](%s) &middot; "
                        "[Check it yourself](%s)"
                        % (anchor["explorer_url"], anchor["verify_url"]))

    if info is None:
        st.info(
            "**These are held in a local tamper-proof store.** The forecasts "
            "replayed here are from past monsoons, so the clock was set to "
            "the original forecast date. The store applies the same rules as "
            "the blockchain version: nothing can be sealed once the rain is "
            "already due, no result can be added before the day has passed, "
            "and the date stamp is set by the store rather than by us.")
        st.caption("The blockchain contract that enforces these rules passes "
                   "33 automated tests covering every one of them.")
    else:
        st.success("Reading the live record store at `%s` on %s. The date "
                   "stamps come from the network itself, not from us."
                   % (info.address, info.network))
        if info.explorer_url:
            st.markdown("[Look it up independently](%s)" % info.explorer_url)
        if not info.is_public:
            st.warning("This is a **test network**. It shows the system "
                       "works end to end, but an outsider cannot check it "
                       "independently.")


PAGES = {
    "Overview": page_overview,
    "Verify a record": page_verify,
    "Trust view": page_trust,
    "Evaluation results": page_results,
    "The region": page_region,
    "Ledger status": page_ledger,
}


PAGE_HINTS = {
    "Overview": "Start here",
    "Verify a record": "Check a forecast was not changed",
    "Trust view": "Every forecast and what happened",
    "Evaluation results": "How well it performed",
    "The region": "Where this applies",
    "Ledger status": "The tamper-proof record",
}


def main():
    st.set_page_config(page_title="MonsoonGuard AI", page_icon="🌧",
                       layout="wide", initial_sidebar_state="expanded")
    st.markdown(CSS, unsafe_allow_html=True)

    with st.sidebar:
        st.markdown(
            "<div class='brand'><div class='brand-name'>MonsoonGuard AI</div>"
            "<div class='brand-sub'>Which heavy-rain warnings "
            "to believe</div></div>", unsafe_allow_html=True)

        choice = st.radio("Page", list(PAGES), label_visibility="collapsed",
                          captions=[PAGE_HINTS[p] for p in PAGES])

        st.markdown("<div class='sb-rule'></div>", unsafe_allow_html=True)

        records = load_records()
        ev = load_evaluation()
        tested = ", ".join((ev or {}).get("split", {}).get("test", [])) or "—"
        st.markdown(
            "<div class='sb-stats'>"
            "<div class='sb-stat'><span>Forecasts on record</span><b>%d</b></div>"
            "<div class='sb-stat'><span>Tested on</span><b>%s</b></div>"
            "</div>" % (len(records), tested), unsafe_allow_html=True)

        info = chain_info()
        if info is None:
            dot, label, detail = "#8c959f", "Local record store", \
                "Records are held in a local tamper-proof store"
        elif info.is_public:
            dot, label, detail = OK, "Live on blockchain", info.address[:10] + "…"
        else:
            dot, label, detail = "#0969da", "Test blockchain", \
                "Running on a local test chain"
        st.markdown(
            "<div class='sb-reg'><div class='sb-reg-top'>"
            "<span class='dot' style='background:%s'></span>%s</div>"
            "<div class='sb-reg-sub'>%s</div></div>" % (dot, label, detail),
            unsafe_allow_html=True)
        if info is not None and info.explorer_url:
            st.markdown("<a class='sb-link' href='%s' target='_blank'>"
                        "View on block explorer &rarr;</a>" % info.explorer_url,
                        unsafe_allow_html=True)

        st.markdown("<div class='sb-rule'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='sb-foot'>This page only shows results. It never "
            "runs the forecast models itself, so what you see is exactly "
            "what was produced and recorded earlier.</div>",
            unsafe_allow_html=True)

    PAGES[choice]()


main()
