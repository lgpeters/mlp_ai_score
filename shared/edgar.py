import requests
import pandas as pd

EDGAR_HEADERS = {"User-Agent": "mlp_ai_score lukepeters98@icloud.com"}


def fetch_json(url: str) -> dict:
    resp = requests.get(url, headers=EDGAR_HEADERS)
    resp.raise_for_status()
    return resp.json()


def fetch_submissions(cik: str) -> dict:
    return fetch_json(f"https://data.sec.gov/submissions/CIK{cik}.json")

def fetch_company_info(cik: str) -> dict:
    submissions = fetch_submissions(cik)
    
def fetch_all_filings(cik: str) -> list[dict]:
    submissions = fetch_submissions(cik)

    recent_filings = submissions.get("filings", {}).get("recent", {})
    historic_filings = submissions.get("filings", {}).get("files", {})

    df_filings = pd.DataFrame(recent_filings)

    if historic_filings:        
        for filings in historic_filings:
            df_filings = pd.concat([df_filings, pd.DataFrame(
                fetch_json(f"https://data.sec.gov/submissions/{filings.get('name')}"))], ignore_index=True)

    return df_filings.to_dict('records')