-- Daily close prices for the universe, from yfinance. closeprice is the
-- SPLIT- AND DIVIDEND-ADJUSTED close (yfinance's auto_adjust=True Close),
-- not the raw historical close -- using raw close would make a stock
-- split look like a 90%+ price crash on the day it happened, which is
-- exactly wrong for any time-series comparison. See pipelines/prices/.
create table if not exists prices (
    ticker     text not null,
    date       date not null,
    closeprice numeric not null,
    primary key (ticker, date)
);
