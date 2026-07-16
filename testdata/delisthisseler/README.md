Arandkei: Historical Delisted Assets Archive
Overview
This dataset provides a meticulously curated collection of historical price data for assets that are no longer listed on public exchanges. In financial modeling, relying only on currently active stocks leads to Survivorship Bias. This archive is designed to mitigate that problem, offering the "missing pieces" of market history needed for realistic backtesting, machine learning training, and econometric research.

Data Coverage
Content: Daily historical OHLCV (Open, High, Low, Close, Volume) data.

Scope: Multi-decade coverage, including records from the 20th and 21st centuries.

Update Frequency: Periodically updated as new assets are delisted or historical records are recovered and processed.

File Structure
Each file follows a standardized naming convention: TICKER_StartDate_EndDate_ARANDKEI.csv.
The data is structured as follows:

Ticker: The stock symbol.

Date: YYYY-MM-DD.

Open, High, Low, Close: Trading prices.

Adj Close: Adjusted price for splits and dividends (where available).

Volume: Number of shares traded.

Technical Note on Adjusted Close
To ensure compatibility with analysis software and backtesting algorithms, the Adj Close column contains no null values. In cases where primary data sources did not provide an adjusted value, the standard Close price has been maintained to ensure time-series continuity.

Support the Project
This archive is the result of a personal and ongoing effort to preserve financial history. If this dataset has been useful for your commercial projects or has saved you hours of data cleaning, consider supporting Arandkei’s research:

☕ [Support Arandkei on Stripe](https://donate.stripe.com/bJeaEY7TWdEOcSxco6d7q01)

License
This dataset is distributed under the Creative Commons Attribution 4.0 International (CC BY 4.0) license. You are free to share and adapt the material for any purpose, even commercially, provided that you give appropriate credit to Arandkei.

"Parte del archivo histórico de Arandkei. Si te sirve, úsalo."
"In Arandkei itlacuilollohuehcapatlacol. Intla mitzpalehuiz, xiquitequipanol."