import pandas as pd
from pathlib import Path

def generate_id(company_name):
    # Simple deterministic ID based on company name
    return str(hash(company_name) & 0xffffffffffff)  # 48-bit positive int

df_universe = pd.read_csv('universe.csv')

companies = ['Adobe Inc (ADBE)',
'Caterpillar Inc (CAT)',
'Dell Inc (DELL)',
'Intel Corporation (INTC)',
'Micron Technology Inc (MU)',
'Nvidia Corporation (NVDA)',
'The Procter & Gamble Company (PG)',
'Atlassian Corporation (TEAM)',
'Waste Management Inc (WM)']

df_universe_additions = pd.DataFrame([{'ID': generate_id(company), 'CompanyName': company} for company in companies])
df_universe_additions = df_universe_additions[df_universe_additions['CompanyName'].isin(df_universe['CompanyName']) == False]
df_universe = pd.concat([df_universe, pd.DataFrame(df_universe_additions)], ignore_index=True)

print(df_universe)