# CX Analyst Playbook (general knowledge)

A practical reference of customer-experience metrics, benchmarks, and analysis
practice. This complements Mohamed's own methods in `work_summary.md`; where they
differ, his definitions win.

## Core metrics - what they mean and how they're computed

- **NPS / tNPS** - loyalty on a 0-10 "would you recommend" question.
  Promoters 9-10, Passives 7-8, Detractors 0-6. Score = %Promoters - %Detractors,
  ranging -100 to +100. tNPS is the same maths but asked right after a specific
  interaction (transactional) rather than about the overall relationship.
- **CSAT** - satisfaction with a specific interaction, usually a 1-5 or 1-10
  scale. A common reporting form is the top-box percentage: count of the top
  ratings (e.g. 4-5 on a 5-point scale) divided by all responses, times 100.
- **CES** - customer effort; how hard it was to get an issue resolved. Lower is
  better. Useful for predicting repeat contacts and churn after service.
- **FCR** - first-contact resolution; share of issues solved on the first
  contact. Strongly tied to satisfaction and to lower repeat-call volume.

## Benchmarks to sanity-check results (industry varies, so compare within sector)

- **NPS:** global average around the low 30s; B2C tends higher than B2B.
  **Telecom is notably low - around the high teens (~19)** - so judge e& numbers
  against telecom peers, not the global average.
- **CSAT:** roughly 75-85% is considered good across sectors; 85%+ is strong.
  Contact-centre leaders often target the 80-90% range.
- **Survey response rate:** ~20-30% is typical; above 40% is strong; below 15%
  is a warning that timing, channel, or survey length need review.

## Reading the numbers like a 10-year analyst

- An absolute score means little alone. Always show **trend** (vs last period),
  **variance vs target**, and **breakdown by segment / channel / agent / queue**.
- A single metric tells part of the story. CSAT (interaction), CES (effort), and
  NPS (loyalty) together give immediate, journey, and long-term views.
- Pair quantitative scores with **VOC verbatims** - the free-text is where the
  "why" lives. Quote a few representative comments per theme.
- Watch sample size before concluding; a segment of a handful of rows is a
  hypothesis, not a finding.

## Telecom churn - drivers and approach

- The usual churn drivers in telecom are **price sensitivity, poor service,
  billing problems, and network/coverage issues**. Retaining an existing
  customer is typically far cheaper than acquiring a new one.
- A standard analytical approach: assemble features (tenure, plan/ARPU, contract
  type, support history, complaint/repeat-contact signals), then train an
  interpretable classifier (logistic regression or random forest). Tenure,
  contract type, and the presence of support/security services are commonly
  among the strongest predictors. Random-forest churn models often land in the
  ~80%+ accuracy range on clean data.
- Don't stop at prediction. Segment by **churn risk**, surface the **drivers**
  (feature importances), and tie each segment to a **personalised retention or
  win-back action**. Generic offers underperform targeted ones.

## Detractor recovery (matches Mohamed's Contact/Reach work)

- After identifying detractors from the survey, the operational questions are:
  did we actually try to reach them (**Contact Rate**), and did we connect
  (**Reach Rate**)? Recovering a detractor quickly can convert them and protect
  revenue, so these operational rates are as important as the score itself.
