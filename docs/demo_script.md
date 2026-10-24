# 3-minute demo script

Deliverable 5 in CLAUDE.md section 12. Timings are generous; a clean run
lands around 2:40, leaving room to speak slowly.

**Before recording**

```bash
streamlit run src/app/streamlit_app.py
```

Have ready in separate tabs or windows: the app, `reports/report.md`, and a
terminal. Record at 1080p or better; the app has small text in tables.

---

## 0:00 – 0:25 · The problem

> "In 2022, floods in Pakistan affected over thirty million people. Forecasts
> existed. The hard question isn't *will it rain heavily* — it's *should I act
> on this particular forecast*."

> "Because a warning that fails repeatedly teaches people to ignore the next
> one. And right now, nothing records what a forecaster claimed, or how
> confident they were, before the outcome was knowable."

**On screen:** title slide, or the report's opening question.

---

## 0:25 – 1:05 · What we found

**On screen:** app → *Evaluation results* page.

> "We compared archived ensemble forecasts against satellite rainfall over
> northern Pakistan, and trained two models: one that predicts false alarms,
> one that predicts misses."

> "On held-out data the underlying forecast catches 74% of extreme rainfall
> events — but 57% of its warnings never verify."

Scroll to the error-model table.

> "Our false-alarm model has a Brier skill score of +0.29 over climatology,
> ROC AUC 0.735. Ensemble disagreement really does predict which warnings
> will fail."

> "The miss model is more honest about its limits. It ranks above chance, but
> adds almost nothing in skill — we report that as a negative result."

**Do not skip:** point at the uncalibrated miss row.

> "And look at this: uncalibrated, that model scored minus 4.86 — far worse
> than guessing. Calibration moved it to plus 0.012. That's why calibration
> is its own stage, not an afterthought."

---

## 1:05 – 1:35 · Trust bands

Scroll to the trust-band table, or show `reports/figures/trust_bands.png`.

> "Turning that into something usable: cells we label LOW trust fail 62% of
> the time, against 25% for MEDIUM. That's a 2.5× separation, capturing 94%
> of all false alarms."

> "But notice the HIGH band is empty. We froze those cutoffs before we knew
> the base error rate would be 57% — so no calibrated model ever lands below
> 20%. We're reporting that as a finding rather than quietly re-tuning the
> bands after seeing results."

That last sentence is worth keeping. Judges notice when limitations are
volunteered rather than extracted.

---

## 1:35 – 2:35 · The audit trail — the strongest part

**On screen:** app → *Verify a record*.

> "Here's the accountability half. Every forecast and its trust label is
> hashed and committed **before** the rain window opens."

Pick any record. Point at the green VERIFIED banner and the checks list.

> "The app recomputes the SHA-256 from the published record and compares it
> with the committed hash, then checks the commit came before the window
> opened. All checks pass."

Open **"Try tampering with it"**. Change `p_wrong_bp` to something much
lower — e.g. 6782 → 500.

> "Now suppose, after the floods, we wanted to claim we'd been more confident
> than we were. Change the probability from 68% to 5%..."

Click re-verify. Let the red MISMATCH sit on screen for a beat.

> "Caught immediately. The hash no longer matches what was committed. You
> don't have to trust us — you can check."

**Then be precise about the limits:**

> "To be clear about what this proves: that this exact record existed,
> unchanged, before the rain. Not that the forecast was good. And an
> owner-controlled registry can't prove we didn't quietly omit a forecast —
> only a public schedule and visible gaps reduce that."

---

## 2:35 – 3:00 · Close

**On screen:** terminal, `pytest -q` finishing, or the repo.

> "163 Python tests plus 24 contract tests, including leakage checks
> that multiply every test-year
> observation by a thousand and assert the thresholds don't move."

> "The honest limitation: ECMWF's tape archive gave us 20 of 40 files in
> eight days and nothing for the years we'd planned to test on, so our test
> set is two months inside one exceptional season. The method is ready; the
> data wasn't obtainable in time."

> "Code, report and every decision we made — including the ones that went
> wrong — are in the repo."

---

## Notes

- **Lead with the tamper demo if you need to cut.** It is the single most
  memorable thing here and it is exactly what the Climate Data &
  Environmental Monitoring track asks for.
- Do not claim a testnet deployment unless one has happened by recording
  time. If it has, add ten seconds showing the contract address on a block
  explorer and say the registry is live on Polygon Amoy.
- Avoid the phrase "predicts floods". We predict *when a forecast is
  unreliable*, which is a narrower and defensible claim.
