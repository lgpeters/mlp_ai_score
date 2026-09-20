-- Daily Fama-French factors + momentum, from Kenneth French's data
-- library (Dartmouth). All values are in PERCENT, exactly as published
-- (e.g. 0.09 means 0.09%, not 0.0009) -- not converted to decimal, to
-- match the source without introducing a conversion-direction bug.
--
-- mkt_rf/rf are shared between the 3-factor and 5-factor files (verified
-- byte-identical across ~15,900 overlapping daily rows before assuming
-- this). smb/hml are NOT shared -- the 5-factor library's SMB/HML use a
-- different sort methodology (2x3x2x2) than the 3-factor library's SMB/HML
-- (2x3), so they're genuinely different series, not duplicates -- kept as
-- separate smb_3/hml_3 vs smb_5/hml_5 columns so either exact
-- specification can be reproduced.
--
-- The Kenneth French library updates this data roughly monthly, not
-- daily, despite the underlying observations being daily-frequency --
-- expect a multi-week lag between "today" and the latest row here.
-- See pipelines/factors/.
create table if not exists factors (
    date  date not null primary key,
    mkt_rf numeric,
    rf     numeric,
    smb_3  numeric,
    hml_3  numeric,
    smb_5  numeric,
    hml_5  numeric,
    rmw    numeric,
    cma    numeric,
    mom    numeric
);
