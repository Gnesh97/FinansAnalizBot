#!/usr/bin/env python3
import json
import csv
from pathlib import Path

# Paths
ROOT_DIR = Path(__file__).resolve().parents[1]
RESEARCH_DATA_DIR = ROOT_DIR / "testdata" / "research_data"
RESEARCH_DATA_DIR.mkdir(parents=True, exist_ok=True)

AV_CSV = RESEARCH_DATA_DIR / "alpha_vantage_delisted.csv"
AV_META = RESEARCH_DATA_DIR / "alpha_vantage_metadata.json"
SEC_TICKERS = RESEARCH_DATA_DIR / "sec_company_tickers_exchange.json"

def main():
    # 1. Generate AV listing status CSV
    # fields: symbol,name,exchange,assetType,ipoDate,delistingDate,status
    av_rows = []
    # 120 normal stocks
    for i in range(120):
        sym = f"STK{i}"
        name = f"Stock Company {i} Inc."
        exch = "NASDAQ" if i % 2 == 0 else "NYSE"
        delist_date = f"2018-05-{10 + (i % 20):02d}"
        av_rows.append([sym, name, exch, "Stock", "2005-01-01", delist_date, "delisted"])
        
    # 10 ETFs (should be excluded as NOT_STOCK)
    for i in range(10):
        av_rows.append([f"ETF{i}", f"ETF Trust {i}", "NYSE ARCA", "ETF", "2010-01-01", "2019-12-01", "delisted"])
        
    # 10 Preferreds/Warrants (should be excluded by symbol/name checks)
    av_rows.append(["STKW+", "Stock Company Warrants", "NASDAQ", "Stock", "2008-01-01", "2020-01-01", "delisted"])
    av_rows.append(["STKPR^", "Stock Company Preferred", "NYSE", "Stock", "2008-01-01", "2020-01-01", "delisted"])
    av_rows.append(["STK.WS", "Stock Company Wts", "NASDAQ", "Stock", "2008-01-01", "2020-01-01", "delisted"])
    for i in range(7):
        av_rows.append([f"STK{i}PR", f"Preferred Stock {i}", "NASDAQ", "Stock", "2008-01-01", "2020-01-01", "delisted"])

    with open(AV_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["symbol", "name", "exchange", "assetType", "ipoDate", "delistingDate", "status"])
        writer.writerows(av_rows)
        
    # AV metadata JSON
    meta = {
        "retrieval_timestamp": "2026-07-10T12:00:00Z",
        "request_parameters": {
            "function": "LISTING_STATUS",
            "state": "delisted",
            "apikey": "******"
        },
        "source_hash": "mock_av_hash_value"
    }
    with open(AV_META, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    # 2. Generate SEC company tickers exchange JSON
    sec_data = []
    for i in range(120):
        cik = 1000000 + i
        sym = f"STK{i}"
        name = f"STOCK COMPANY {i} INC"
        exch = "NASDAQ" if i % 2 == 0 else "NYSE"
        
        # Mismatch cases
        if i == 5:
            # POSSIBLE_TICKER_REUSE: conformed name completely different
            name = "DIFFERENT WIDGETS CORP"
        elif i == 6:
            # MULTIPLE_CIK_CANDIDATES: add two rows with different CIKs
            sec_data.append([1000006, "STOCK COMPANY 6 INC", "STK6", "NASDAQ"])
            sec_data.append([3000006, "STOCK COMPANY 6 ANOTHER INC", "STK6", "NASDAQ"])
            continue
        elif i == 7:
            # NO_SEC_MATCH: skip entirely
            continue
        elif i == 8:
            # NAME_EXCHANGE_MATCH: ticker in SEC differs (e.g. STK8OLD), but name matches
            sym = "STK8OLD"
            
        sec_data.append([cik, name, sym, exch])

    sec_tickers_json = {
        "fields": ["cik", "name", "ticker", "exchange"],
        "data": sec_data
    }
    with open(SEC_TICKERS, "w", encoding="utf-8") as f:
        json.dump(sec_tickers_json, f, indent=2)

    # 3. Generate SEC Submissions JSON files for the CIKs
    for i in range(120):
        if i == 7:
            continue
        cik = 1000000 + i
        cik_str = str(cik).zfill(10)
        
        name = f"STOCK COMPANY {i} INC"
        sym = f"STK{i}"
        exch = "NASDAQ" if i % 2 == 0 else "NYSE"
        
        if i == 5:
            name = "DIFFERENT WIDGETS CORP"
        elif i == 8:
            sym = "STK8OLD"

        # Mock filing dates matching delistingDate range (May 2018)
        delist_date = f"2018-05-{10 + (i % 20):02d}"
        f25_date = delist_date
        f15_date = f"2018-06-{10 + (i % 20):02d}"

        sub_json = {
            "cik": cik_str,
            "name": name,
            "tickers": [sym],
            "exchanges": [exch],
            "filings": {
                "recent": {
                    "form": ["10-K", "25-NSE", "15-12B"],
                    "filingDate": ["2018-03-15", f25_date, f15_date]
                }
            }
        }
        
        # Write CIK submissions file
        cik_file = RESEARCH_DATA_DIR / f"sec_submissions_{cik_str}.json"
        with open(cik_file, "w", encoding="utf-8") as f:
            json.dump(sub_json, f, indent=2)

    # Write CIK submission files for CIK 3000006
    with open(RESEARCH_DATA_DIR / "sec_submissions_03000006.json", "w", encoding="utf-8") as f:
        json.dump({
            "cik": "03000006",
            "name": "STOCK COMPANY 6 ANOTHER INC",
            "tickers": ["STK6"],
            "exchanges": ["NASDAQ"],
            "filings": {"recent": {"form": [], "filingDate": []}}
        }, f, indent=2)

    print("Mock SEC and AV cache files generated successfully under testdata/research_data/.")

if __name__ == "__main__":
    main()
