"""UI tests for the Streamlit app.

These were missing. Until now the app was only ever checked for an HTTP 200,
which says the server started - not that a page renders anything, and not
that it renders the right thing. A page could raise, show an empty table, or
quietly display stale numbers and still return 200.

AppTest runs the real app in-process and inspects what it produced, so these
are black-box tests of rendered output rather than white-box tests of the
functions behind it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APP = str(ROOT / "src" / "app" / "streamlit_app.py")
PAGES = ["Overview", "Verify a record", "Trust view", "Evaluation results",
         "The region", "Ledger status"]


def run_page(name: str):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.sidebar.radio[0].set_value(name).run()
    return at


@pytest.fixture(scope="module")
def pages():
    return {name: run_page(name) for name in PAGES}


# --- every page renders ------------------------------------------------------

@pytest.mark.parametrize("name", PAGES)
def test_page_raises_no_exception(pages, name):
    at = pages[name]
    assert not at.exception, "%s raised: %s" % (
        name, at.exception[0].value if at.exception else "")


@pytest.mark.parametrize("name", PAGES)
def test_page_shows_no_error_box(pages, name):
    at = pages[name]
    assert not at.error, "%s shows st.error: %s" % (
        name, at.error[0].value if at.error else "")


@pytest.mark.parametrize("name", PAGES)
def test_page_actually_renders_content(pages, name):
    """An empty page returns HTTP 200 just as happily as a full one."""
    at = pages[name]
    rendered = len(at.markdown) + len(at.dataframe) + len(at.metric)
    assert rendered >= 5, "%s rendered almost nothing (%d elements)" % (name, rendered)


# --- the data-driven pages show real data ------------------------------------

def test_trust_view_lists_records(pages):
    at = pages["Trust view"]
    assert len(at.dataframe) >= 1
    assert len(at.dataframe[0].value) > 0, "trust table is empty"


def test_region_page_shows_the_full_grid(pages):
    """15 latitudes x 13 longitudes. A silently truncated grid would still
    render."""
    at = pages["The region"]
    assert len(at.dataframe) >= 1
    grid = at.dataframe[0].value
    assert grid.shape == (15, 13), "region grid is %s, expected (15, 13)" % (grid.shape,)


def test_ledger_lists_committed_records(pages):
    at = pages["Ledger status"]
    assert len(at.dataframe) >= 1
    assert len(at.dataframe[0].value) > 0, "ledger table is empty"


# --- the app must not leak implementation detail ------------------------------

@pytest.mark.parametrize("name", PAGES)
def test_no_file_paths_or_script_names_in_the_ui(pages, name):
    """Users should never be shown repository paths or script invocations."""
    at = pages[name]
    text = " ".join(m.value for m in at.markdown if isinstance(m.value, str))
    text += " ".join(w.value for w in at.warning if isinstance(w.value, str))
    text += " ".join(i.value for i in at.info if isinstance(i.value, str))
    for leak in ("reports/", "records/", "scripts/", "data/raw", ".parquet",
                 "python scripts", "src/core"):
        assert leak not in text, "%s exposes %r in the UI" % (name, leak)


def test_no_raw_json_dump_on_the_verify_page(pages):
    """The record is shown as structured content, not a JSON blob."""
    at = pages["Verify a record"]
    assert len(at.json) == 0, "verify page dumps raw JSON"


# --- the headline numbers come from the real evaluation ----------------------

def test_overview_reports_the_committed_evaluation(pages):
    """Guards against the app and the report disagreeing: the dashboard must
    show whatever split the saved evaluation actually used."""
    import json
    results = ROOT / "reports" / "evaluation_results.json"
    if not results.exists():
        pytest.skip("no evaluation results")
    ev = json.loads(results.read_text(encoding="utf-8"))
    fa = ev["targets"]["false_alarm"]["metrics"]["calibrated"]

    at = pages["Overview"]
    text = " ".join(m.value for m in at.markdown if isinstance(m.value, str))
    assert "%.3f" % fa["roc_auc"] in text, \
        "overview does not show the evaluation's false-alarm AUC"
