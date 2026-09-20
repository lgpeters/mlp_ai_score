# SEC coverage exceptions

Known gaps/quirks in SEC filing coverage for the current universe. Not bugs —
documented here so they aren't rediscovered as if they were.

## Dell (DELL)

Dell's SEC history spans two CIKs, tracked via `company_cik_aliases`:

- **CIK `0000826083`** ("Dell Inc") — filed through **2013-10-29**, when the
  company went private in a Michael Dell + Silver Lake LBO and deregistered.
- **CIK `0001571996`** ("Dell Technologies Inc") — the new entity created for
  the 2016 EMC acquisition, financed partly via a publicly traded tracking
  stock (ticker DVMT) tied to its VMware stake. That public stock triggered
  SEC reporting again: an S-4 in Dec 2015, then continuous 10-Qs from mid-2016
  onward. The company converted the tracking stock into ordinary common stock
  (NYSE: DELL) in December 2018 and has filed continuously since.

**Real gap: November 2013 – November 2015.** Dell was fully private with no
public securities during this window — no filings exist anywhere for it,
under any entity, because there was no reporting obligation. This is not
fixable by finding another CIK; it reflects genuine private-company status.

## Atlassian (TEAM)

Atlassian IPO'd in **December 2015**. It has no SEC filings before that date
— not a gap, just pre-IPO. It was also a UK plc (foreign private issuer)
until a September 2022 Delaware redomiciliation, filing `20-F`/`6-K`
(foreign-issuer forms) instead of `10-K`/`10-Q`/`8-K` up to that point —
same CIK throughout, just a different form type. See the main
`pipelines/sec/README.md` for why `20-F`/`20-F/A` are in `FORM_TYPES`.
