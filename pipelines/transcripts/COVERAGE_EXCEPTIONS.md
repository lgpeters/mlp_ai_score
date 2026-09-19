# Transcript coverage exceptions

Known gaps/quirks in earnings call transcript coverage from stockanalysis.com
(sourced from Quartr, a third-party vendor). Indexed by *ticker*, not CIK —
a different addressing scheme from the SEC pipeline, so gaps here don't
necessarily line up with SEC filing gaps even for the same company. Not
bugs — documented here so they aren't rediscovered as if they were.

## Caterpillar (CAT)

Coverage on this source starts **2019-04-24**, despite Caterpillar having
been a public company (NYSE: CAT) since long before then. Checked for a
scraping/pagination cutoff on our end (none found — no `limit`/`page`/
`hasMore` fields in the underlying data) and confirmed the page explicitly
attributes transcripts to Quartr. This is a genuine limitation of Quartr's
own coverage for this ticker, not a real business-history gap. No fix
available through this source — CAT's pre-2019 calls would need a different
provider entirely.

## Dell (DELL)

Coverage on this source starts **2022**, a much larger gap than Caterpillar's
and layered on top of Dell's own real corporate history (see
`pipelines/sec/COVERAGE_EXCEPTIONS.md` for the full CIK/LBO/re-IPO timeline):

- Nov 2013 – Nov 2015: no calls held at all (Dell was private) — a real gap.
- Dec 2015 – Dec 2018: Dell Technologies was public but traded under ticker
  **DVMT** (a VMware tracking stock), not DELL. stockanalysis.com has no
  page for `dvmt` (404, unindexed) — unlike the SEC pipeline, there's no
  alias mechanism available here to recover this era.
- 2019 – 2021: Dell was trading as NYSE: DELL by this point, but Quartr's
  own coverage for the `dell` slug still doesn't start until 2022 — an
  unexplained vendor gap, same category as Caterpillar's.

Net effect: no usable transcripts for Dell before 2022, despite the company
having been a real, calls-holding public entity for most of 2015 onward.

## Atlassian (TEAM)

Atlassian IPO'd in **December 2015**; earliest available transcript is 2016.
This is a real, expected boundary (pre-IPO, not a coverage gap) — same
underlying reason as its SEC filing history starting at the same point. See
`pipelines/sec/COVERAGE_EXCEPTIONS.md`.
