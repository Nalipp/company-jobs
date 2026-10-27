# Post-onboarding job calibration loop

Status: future product idea; not approved for MVP implementation.

## Problem

An onboarding interview can capture explicit requirements, but users may not know
how to describe all of their preferences in the abstract. Reviewing real jobs can
surface distinctions that are difficult to elicit with direct questions—for
example, liking a role's work while disliking the company's team composition or
cultural language.

## Possible experience

After the initial onboarding draft, the product could run a small calibration pass:

1. Collect a representative set of plausible jobs using the draft profile.
2. Ask the user to review each job and record what they like, dislike, and whether
   they would pass or pursue it.
3. Extract preference hypotheses across dimensions such as work content, product
   orientation, culture, company stage, domain relevance, and risk tolerance.
4. Show the hypotheses and their supporting job feedback to the user.
5. Let the user approve, revise, or reject each proposed profile change before it
   affects later discovery or ranking.

This should be a calibration loop, not an automatic profile rewrite. A small set of
job reactions can be noisy, internally inconsistent, or driven by details that are
missing from the posting.

## Potential value

- Converts abstract preferences into reactions to concrete evidence.
- Reveals tradeoffs between appealing responsibilities and concerning culture.
- Gives the ranking system more clarity about subtle preferences.
- Lets users correct the system before a larger discovery run.
- Produces explainable profile refinements linked to specific examples.

## Minimal experiment before productizing

The concept can be tested manually without adding a new command or schema:

- Present 8–12 varied, plausible jobs after onboarding.
- Ask for a verdict plus short liked/disliked excerpts.
- Summarize no more than five preference hypotheses.
- Ask whether those hypotheses would have improved the original profile.
- Compare the next ranked results with and without the approved refinements.

Only consider building the feature if this manual test consistently changes ranking
quality enough to justify the added onboarding effort.

## Possible future data shape

A job-feedback record might contain:

- canonical job URL and company/title
- verdict such as `pursue`, `pass`, or `uncertain`
- quoted or paraphrased positive signals
- quoted or paraphrased negative signals
- free-form explanation
- review timestamp

An extracted hypothesis might contain:

- proposed preference or constraint
- confidence
- supporting and conflicting feedback records
- whether the user approved it

These are design notes only. They do not modify `profiles/default.json` or current
discovery behavior.

## Open product questions

- Does calibration improve results more than a few additional onboarding questions?
- How much review work will users tolerate before seeing useful results?
- How should the system avoid overfitting to a small or unrepresentative job set?
- Should calibration examples intentionally vary company size, stage, and culture?
- How should ambiguous feedback be clarified without turning calibration into a
  lengthy interview?
- How should expired or changed postings remain auditable over time?
- Where should approved feedback live if the concept graduates from a product note
  into persisted workflow data?

## MVP decision

Do not build this yet. Preserve the concept and the initial feedback evidence so it
can inform future product decisions and a lightweight manual experiment.
