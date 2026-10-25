# 3-minute demo script

Deliverable 5 in CLAUDE.md section 12. Timings are generous; a clean run
lands around 2:45, leaving room to speak slowly.

Every number below is read from `reports/evaluation_results.json` and
`reports/evaluation_results_2022split.json`. If you re-run the evaluation,
re-check them before recording. Do not round in your favour.

**Before recording**

```bash
streamlit run src/app/streamlit_app.py
```

Have ready in separate tabs: the app and a terminal. Record at 1080p or
better — the tables have small text.

---

## 0:00 – 0:25 · The problem

> "In 2022, floods in Pakistan affected more than thirty million people.
> Forecasts existed. The hard question was never *will it rain heavily* —
> it's *should I act on this particular warning*."

> "Because warnings that keep failing teach people to ignore the next one.
> And nothing today records what was claimed, or how confident anyone was,
> before the outcome could be known."

**On screen:** app → *Overview*, the opening headline.

---

## 0:25 – 0:50 · How bad the warnings actually are

**On screen:** stay on *Overview*, the three cards.

> "First, the baseline. Over northern Pakistan in our test month, the
> ensemble forecast caught 39% of extreme rainfall days — but 96% of the
> warnings it issued never verified. It gave about eleven warnings for every
> real event."

> "We are not trying to beat that forecast. That's for weather centres. We're
> trying to say, in advance, which of those eleven to act on."

---

## 0:50 – 1:30 · What we found — and where it broke

**On screen:** *Evaluation results*.

> "We trained two models: one that predicts false alarms, one that predicts
> misses. Both score a single number — the probability this decision is
> wrong."

> "Trained on the 2021 monsoon and tested on the peak of 2022 — a different
> year, but the same kind of weather — the false-alarm model reaches a Brier
> skill score of +0.29 over climatology, ROC AUC 0.735, on seven thousand
> samples. Ensemble disagreement genuinely predicts which warnings fail."

> "And it was usable. Warnings it rated low trust were wrong 62% of the
> time; medium trust, 25%. One band held 94% of all the false alarms."

> "The miss model does not work. Plus 0.012 — essentially nothing. We report
> that as a negative result rather than dropping it."

**Then the part that matters most:**

> "Then we tested on a month the model had never seen — June 2024. And it
> broke. Discrimination survived: AUC 0.740, almost unchanged. But every
> single sample landed in one trust band, so the output was useless in
> practice."

> "We know why, and it isn't the model. We define 'extreme' as the 95th
> percentile across the whole season, and the season is dominated by its
> wettest months. June is far drier, so that bar is set much too low for
> June — the event rate was 1.25% against 5.33% in training. The measuring
> stick is wrong, not the scoring."

That diagnosis is worth the airtime. Judges notice a team that can tell the
difference between a broken model and a broken definition.

---

## 1:30 – 2:20 · The audit trail — the strongest part

**On screen:** *Verify a record*.

> "Here's the accountability half. Every forecast and its trust rating is
> hashed and sealed before the rain window opens."

Pick any record. Point at the green banner and the list of checks.

> "The page recomputes the SHA-256 from the published record, compares it
> with what was sealed, and confirms the seal came first. All checks pass."

Scroll to **"Could we just change it afterwards?"** and press the button.

> "Now suppose that after the flood we wanted to look better, and quietly
> rewrote this forecast to claim we'd been confident all along — dropping
> our 88% chance of being wrong down to 5%."

**Let the red failure sit on screen for a beat.** The two cards above it
show what was locked against what we tried to claim.

> "Caught instantly. You don't have to trust us. You can check."

**Then be precise about the limits — do not skip this:**

> "To be exact about what that proves: this record existed, unchanged,
> before the rain. Not that the forecast was good. And an owner-controlled
> registry can't prove we never quietly dropped an inconvenient forecast —
> only a published schedule and visible gaps reduce that."

---

## 2:20 – 2:45 · Sealed in Bitcoin

**On screen:** *Ledger status*, the fingerprint section.

> "The 2022 records are a replay — the rain already fell, so no timestamp
> made today could prove we spoke first, and the contract correctly refuses
> to pretend otherwise."

> "What we can prove is that these results haven't been edited since we
> published them. All twenty-four records fold into one fingerprint, and
> that fingerprint is written into the Bitcoin blockchain. Change any number
> anywhere and it no longer matches."

Show the block link.

> "Bitcoin block 972,977, mined on the 24th of October. Anyone can check it
> with the proof file in the repository — no account, no permission from us,
> and nothing we can revoke."

Confirmed on 2026-10-24 at 21:28 UTC, a day before the deadline, which is
itself part of the claim: the results were fixed before submission.

---

## 2:45 – 3:00 · Close

**On screen:** terminal with `pytest -q` and `npx hardhat test` finishing.

> "207 Python tests and 33 contract tests, including leakage checks that
> multiply every test-period observation by a thousand and assert the
> thresholds don't move."

> "The honest limitation: ECMWF's archive gave us 22 of 40 files in eight
> days, and nothing for the years we'd planned to test on. Our test is one
> month. The method is ready; the data wasn't obtainable in time."

> "Code, report, and every decision — including the ones that went wrong —
> are in the repo."

---

## Notes

- **If you must cut, keep the tamper demo and the June-2024 failure.** The
  first is the most memorable thing here; the second is what separates an
  honest project from a demo.
- **Do not claim a public deployment that hasn't happened.** If the contract
  is live on a testnet by recording time, add ten seconds on the block
  explorer showing the address. If it isn't, say the registry runs against a
  local chain with the full test suite proving its rules — that is true and
  defensible.
- **Do not say the Bitcoin block "contains our data".** It holds a combined
  fingerprint covering thousands of unrelated files; the proof file links
  ours to it. Say "anchored in block N, verifiable with the included proof".
- Avoid "predicts floods". We predict *when a forecast is unreliable* — a
  narrower claim, and one we can actually defend.
