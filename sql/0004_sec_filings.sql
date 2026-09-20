create table if not exists sec_filings (
    content_id              uuid primary key references content(content_id),
    accession_number        text,
    filing_date              date,
    report_date             date,
    acceptance_date_time    timestamptz,
    act                     text,
    form                    text,
    file_number             text,
    film_number             text,
    items                   text,
    core_type               text,
    size                    integer,
    is_xbrl                 boolean,
    is_inline_xbrl          boolean,
    is_xbrl_numeric         boolean,
    primary_document        text,
    primary_doc_description text
);
