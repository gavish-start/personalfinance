import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import json
import plotly.express as px
import re
import io

# ==========================================
# PAGE CONFIGURATION & THEME CUSTOMIZATION
# ==========================================
st.set_page_config(page_title="Global Portfolio Analyzer", page_icon="🌍", layout="wide")

# Custom CSS to force clean Zerodha-style design
st.markdown("""
    <style>
    /* Metric Cards - Kite style */
    .stMetric {
        background-color: #fcfcfc;
        padding: 16px;
        border-radius: 4px;
        border: 1px solid #f0f0f0;
    }
    div[data-testid="stSidebar"] {
        background-color: #fcfcfc;
        border-right: 1px solid #f0f0f0;
    }
    /* Hide top margin/padding for cleaner dashboard spacing */
    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    /* Clean Cards for onboarding */
    .onboarding-card {
        background-color: #fcfcfc;
        border: 1px solid #e0e0e0;
        border-left: 4px solid #387ed1;
        padding: 20px;
        border-radius: 4px;
        margin-bottom: 20px;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 1. SESSION STATE INITIALIZATION
# ==========================================
if "df_portfolio" not in st.session_state:
    st.session_state.df_portfolio = None
if "df_timeline" not in st.session_state:
    st.session_state.df_timeline = None
if "fx_rate" not in st.session_state:
    st.session_state.fx_rate = 105.80

# ==========================================
# 2. MARKET DATA INTEGRATION (Cached for speed)
# ==========================================
@st.cache_data(ttl=3600)
def get_exchange_rate():
    try:
        return yf.Ticker("GBPINR=X").fast_info['last_price']
    except:
        return 105.80 # Fallback

@st.cache_data(ttl=3600)
def get_live_stock_price(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        return ticker.fast_info['last_price']
    except:
        return None

@st.cache_data(ttl=3600)
def get_mutual_fund_nav(scheme_name):
    # Standard Indian AMFI mutual fund codes
    code_map = {
        "Kotak Focused Fund Gr": "147473",
        "SBI Small Cap Fund Reg Growth": "119616",
        "Kotak ELSS Tax Saver Fund - Gr": "119773" 
    }
    scheme_code = code_map.get(scheme_name)
    if not scheme_code: 
        return None
    try:
        response = requests.get(f"https://api.mfapi.in/mf/{scheme_code}").json()
        return float(response['data'][0]['nav'])
    except:
        return None

# ==========================================
# 3. HELPER CATEGORIZATION UTILITY
# ==========================================
def get_cap_category(name, asset_class, geo):
    """Dynamically maps any stock, ETF, or fund to a market cap or global segment."""
    name_lower = name.lower()
    if 'cash' in name_lower:
        return 'Cash'
        
    # Global Asset mapping (including ETFs and international holdings)
    if (geo == 'UK' or 'vanguard' in name_lower or 'vwrp' in name_lower or 
        '.l' in name_lower or 'global' in name_lower or 'us tech' in name_lower or 
        'nasdaq' in name_lower or 'mon100' in name_lower or 'sp500' in name_lower):
        return 'Global Funds'
        
    # Small Cap assets
    if 'small' in name_lower or 'smallcap' in name_lower:
        return 'Small Cap'
        
    # Mid Cap assets
    if 'mid' in name_lower or 'midcap' in name_lower or 'idfcfirstb' in name_lower or 'granules' in name_lower:
        return 'Mid Cap'
        
    # Default fallback mapping based on standard Large Cap assets or Equity class
    if (asset_class == 'Equity' or 'large' in name_lower or 
        'focused' in name_lower or 'elss' in name_lower or 'bluechip' in name_lower):
        return 'Large Cap'
        
    return 'Multi Cap / Other'

# ==========================================
# 4. RAW STATEMENT PARSERS (Robust CSV & Excel)
# ==========================================
def parse_zerodha_file(uploaded_file):
    try:
        if uploaded_file.name.endswith('.xlsx') or uploaded_file.name.endswith('.xls'):
            df_raw = pd.read_excel(uploaded_file, header=None)
            header_idx = None
            for idx, row in df_raw.iterrows():
                if row.astype(str).str.contains("Symbol", case=False, na=False).any():
                    header_idx = idx
                    break
            if header_idx is not None:
                headers = df_raw.iloc[header_idx].astype(str).str.strip().tolist()
                df = df_raw.iloc[header_idx + 1:].copy()
                df.columns = headers
            else:
                df = pd.read_excel(uploaded_file)
        else:
            lines = uploaded_file.getvalue().decode("utf-8").split("\n")
            header_idx = 0
            for i, line in enumerate(lines):
                if "Symbol" in line:
                    header_idx = i
                    break
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, skiprows=header_idx)
        
        df.columns = [str(col).strip().lstrip(',').rstrip(',') for col in df.columns]
        if 'Symbol' in df.columns:
            df = df.dropna(subset=['Symbol'])
        else:
            symbol_candidates = [c for c in df.columns if 'symbol' in c.lower()]
            if symbol_candidates:
                df = df.dropna(subset=[symbol_candidates[0]])
                df = df.rename(columns={symbol_candidates[0]: 'Symbol'})
        
        symbol_col = 'Symbol'
        qty_col = [c for c in df.columns if 'Quantity' in c and 'Available' in c]
        qty_col = qty_col[0] if qty_col else ('Quantity Available' if 'Quantity Available' in df.columns else df.columns[4])
        avg_col = [c for c in df.columns if 'Average Price' in c or 'Average' in c]
        avg_col = avg_col[0] if avg_col else ('Average Price' if 'Average Price' in df.columns else df.columns[10])
        close_col = [c for c in df.columns if 'Closing Price' in c or 'Previous Closing Price' in c]
        close_col = close_col[0] if close_col else None

        holdings = []
        for _, row in df.iterrows():
            if pd.isna(row[symbol_col]):
                continue
            symbol = str(row[symbol_col]).strip()
            if not symbol or symbol.lower() in ['symbol', 'nan']:
                continue
            try:
                qty = float(row[qty_col])
                avg_cost = float(row[avg_col])
            except (ValueError, TypeError):
                continue
            fallback_price = float(row[close_col]) if close_col and pd.notna(row[close_col]) else avg_cost
            
            holdings.append({
                'name': symbol,
                'class': 'Equity',
                'geo': 'India',
                'qty': qty,
                'avgCost': avg_cost,
                'currentPrice': fallback_price,
                'currency': 'INR',
                'source': 'Zerodha'
            })
        return holdings
    except Exception as e:
        st.error(f"Error parsing Zerodha statement: {e}")
        return []

def parse_cams_excel(uploaded_file):
    """Parses CAMS holdings workbook sheet 'Summary' or dynamic CSV layouts."""
    try:
        if uploaded_file.name.endswith('.csv'):
            lines = uploaded_file.getvalue().decode("utf-8").split("\n")
            header_idx = 0
            for i, line in enumerate(lines):
                if "FundName" in line or "Fund Name" in line:
                    header_idx = i
                    break
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, skiprows=header_idx)
        else:
            xls = pd.ExcelFile(uploaded_file)
            sheet_name = 'Summary' if 'Summary' in xls.sheet_names else xls.sheet_names[0]
            df_raw = pd.read_excel(xls, sheet_name=sheet_name, header=None)
            
            header_idx = None
            for idx, row in df_raw.iterrows():
                if row.astype(str).str.contains("FundName|Fund Name", case=False, na=False).any():
                    header_idx = idx
                    break
            
            if header_idx is not None:
                headers = df_raw.iloc[header_idx].astype(str).str.strip().tolist()
                df = df_raw.iloc[header_idx + 1:].copy()
                df.columns = headers
            else:
                df = pd.read_excel(xls, sheet_name=sheet_name)
        
        # Clean columns
        df.columns = [str(col).strip().lstrip(',').rstrip(',') for col in df.columns]
        
        # Safely align scheme name column
        if 'FundName' in df.columns:
            df = df.dropna(subset=['FundName'])
        elif 'Fund Name' in df.columns:
            df = df.dropna(subset=['Fund Name'])
            df = df.rename(columns={'Fund Name': 'FundName'})
        else:
            candidates = [c for c in df.columns if 'fund' in c.lower()]
            if candidates:
                df = df.dropna(subset=[candidates[0]])
                df = df.rename(columns={candidates[0]: 'FundName'})
        
        fund_col = 'FundName'
        qty_col = [c for c in df.columns if 'Available Units' in c or 'Units' in c]
        qty_col = qty_col[0] if qty_col else 'Available Units'
        
        avg_col = [c for c in df.columns if 'Average NAV' in c or 'Avg. NAV' in c or 'WtgAvg' in c]
        avg_col = avg_col[0] if avg_col else 'Average NAV'
        
        current_col = [c for c in df.columns if 'Current NAV' in c or 'NAV' in c]
        current_col = current_col[0] if current_col else 'Current NAV'
        
        # Capture ISIN values if present
        isin_col = [c for c in df.columns if 'isin' in c.lower() or 'isin number' in c.lower()]
        isin_col = isin_col[0] if isin_col else None
        
        holdings = []
        for _, row in df.iterrows():
            fund_name = str(row[fund_col]).strip()
            if not fund_name or fund_name.lower() in ['fundname', 'fund name', 'total', 'nan'] or 'total' in fund_name.lower():
                continue
            try:
                qty = float(str(row[qty_col]).replace(',', ''))
                avg_cost = float(str(row[avg_col]).replace(',', ''))
                current_price = float(str(row[current_col]).replace(',', ''))
            except (ValueError, TypeError, KeyError):
                continue
            
            isin_val = str(row[isin_col]).strip() if isin_col and pd.notna(row[isin_col]) else None
            
            if qty > 0:
                holdings.append({
                    'name': fund_name,
                    'class': 'Mutual Fund',
                    'geo': 'India',
                    'qty': qty,
                    'avgCost': avg_cost,
                    'currentPrice': current_price,
                    'currency': 'INR',
                    'source': 'CAMS',
                    'isin': isin_val if isin_val and isin_val.lower() != 'nan' else None
                })
        return holdings
    except Exception as e:
        st.error(f"Error parsing CAMS file: {e}")
        return []

def parse_cams_details_for_timeline(uploaded_file):
    """Parses transaction logs on 'Details' sheet for chronological cash flows."""
    try:
        if uploaded_file.name.endswith('.csv'):
            lines = uploaded_file.getvalue().decode("utf-8").split("\n")
            header_idx = None
            for i, line in enumerate(lines):
                if "Transaction Date" in line:
                    header_idx = i
                    break
            if header_idx is None:
                return []
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, skiprows=header_idx)
        else:
            xls = pd.ExcelFile(uploaded_file)
            if 'Details' not in xls.sheet_names:
                return []
            df_raw = pd.read_excel(xls, sheet_name='Details', header=None)
            header_idx = None
            for idx, row in df_raw.iterrows():
                if row.astype(str).str.contains("Transaction Date", case=False, na=False).any():
                    header_idx = idx
                    break
            if header_idx is None:
                return []
            headers = df_raw.iloc[header_idx].astype(str).str.strip().tolist()
            df = df_raw.iloc[header_idx + 1:].copy()
            df.columns = headers
            
        df.columns = [str(col).strip().lstrip(',').rstrip(',') for col in df.columns]
        df = df.dropna(subset=['Transaction Date', 'Amount'])
        
        txns = []
        for _, row in df.iterrows():
            date_str = str(row['Transaction Date']).strip()
            if date_str.lower() in ['transaction date', 'nan', '']:
                continue
            try:
                tx_date = pd.to_datetime(date_str).strftime('%Y-%m-%d')
                amount = float(str(row['Amount']).replace(',', ''))
            except:
                continue
            
            # Record deposits (buys/additional cash contributions)
            if amount > 0:
                txns.append({
                    'date': tx_date,
                    'amount_inr': amount,
                    'source': 'CAMS'
                })
        return txns
    except:
        return []

def parse_trading212_file(uploaded_file):
    try:
        df = pd.read_csv(uploaded_file)
        if 'Time' in df.columns:
            df = df.sort_values(by='Time')
            
        cash = 0.0
        holdings_dict = {}
        
        for _, row in df.iterrows():
            action = str(row.get('Action', '')).strip()
            total = float(row.get('Total', 0))
            ticker = str(row.get('Ticker', '')).strip() if pd.notna(row.get('Ticker')) else ''
            name = str(row.get('Name', '')).strip() if pd.notna(row.get('Name')) else ''
            shares = float(row.get('No. of shares', 0)) if pd.notna(row.get('No. of shares')) else 0
            
            if action == 'Deposit':
                cash += total
            elif action == 'Withdrawal':
                cash -= total
            elif action in ['Interest on cash', 'Dividend', 'Dividend (Ordinary)']:
                cash += total
            elif action in ['Market buy', 'Limit buy']:
                if ticker:
                    if ticker not in holdings_dict:
                        holdings_dict[ticker] = {'qty': 0.0, 'total_cost': 0.0, 'name': name}
                    holdings_dict[ticker]['qty'] += shares
                    holdings_dict[ticker]['total_cost'] += total
                cash -= total
            elif action in ['Market sell', 'Limit sell']:
                if ticker and ticker in holdings_dict:
                    ratio = shares / holdings_dict[ticker]['qty'] if holdings_dict[ticker]['qty'] > 0 else 1
                    holdings_dict[ticker]['qty'] -= shares
                    holdings_dict[ticker]['total_cost'] -= (holdings_dict[ticker]['total_cost'] * ratio)
                cash += total
                
        holdings = []
        for ticker, details in holdings_dict.items():
            if details['qty'] > 0:
                avg_cost = details['total_cost'] / details['qty']
                holdings.append({
                    'name': f"{ticker}.L" if not ticker.endswith('.L') else ticker,
                    'class': 'Global ETF' if 'Vanguard' in details['name'] or 'ETF' in details['name'] else 'Equity',
                    'geo': 'UK',
                    'qty': details['qty'],
                    'avgCost': avg_cost,
                    'currentPrice': avg_cost, 
                    'currency': 'GBP',
                    'source': 'Trading 212'
                })
        if cash > 0:
            holdings.append({
                'name': 'Uninvested Cash (ISA)',
                'class': 'Cash',
                'geo': 'UK',
                'qty': cash,
                'avgCost': 1.0,
                'currentPrice': 1.0,
                'currency': 'GBP',
                'source': 'Trading 212'
            })
        return holdings
    except Exception as e:
        st.error(f"Error parsing Trading 212 statement: {e}")
        return []

def parse_trading212_txns_for_timeline(uploaded_file):
    """Parses Trading 212 cash deposits for ISA chronological contributions."""
    try:
        uploaded_file.seek(0)
        df = pd.read_csv(uploaded_file)
        if 'Time' in df.columns:
            df = df.sort_values(by='Time')
            
        txns = []
        for _, row in df.iterrows():
            action = str(row.get('Action', '')).strip()
            total = float(row.get('Total', 0))
            time_str = str(row.get('Time', '')).strip()
            
            if action == 'Deposit' and total > 0:
                try:
                    tx_date = pd.to_datetime(time_str).strftime('%Y-%m-%d')
                    txns.append({
                        'date': tx_date,
                        'amount_gbp': total,
                        'source': 'Trading 212'
                    })
                except:
                    continue
        return txns
    except:
        return []

# ==========================================
# 5. PROCESSING PIPELINE
# ==========================================
def process_holdings(raw_data, fx_rate):
    processed = []

    # Stand-in dictionary of mutual fund ISIN maps for fallbacks
    isin_map = {
        "SBI Small Cap Fund Reg Growth": "INF204K01202",
        "Kotak Focused Fund Gr": "INF204K01RU2",
        "Kotak ELSS Tax Saver Fund - Gr": "INF174K01LS2"
    }

    for item in raw_data:
        rate = fx_rate if item['currency'] == 'GBP' else 1
        
        current_price = item['currentPrice']
        if item['class'] == 'Equity' and item['geo'] == 'India':
            live_price = get_live_stock_price(f"{item['name']}.NS")
            if live_price: current_price = live_price
        elif item['class'] == 'Mutual Fund':
            live_nav = get_mutual_fund_nav(item['name'])
            if live_nav: current_price = live_nav
        elif item['class'] == 'Global ETF' or (item['class'] == 'Equity' and item['geo'] == 'UK'):
            live_price = get_live_stock_price(item['name'])
            if live_price: current_price = live_price

        total_invested = (item['qty'] * item['avgCost']) * rate
        current_val = (item['qty'] * current_price) * rate
        pnl = current_val - total_invested
        pnl_pct = (pnl / total_invested) * 100 if total_invested > 0 else 0
        
        # Calculate market cap categorization dynamically
        cap_cat = get_cap_category(item['name'], item['class'], item['geo'])
        
        # Dynamic factsheet links: Trendlyne for Indian Assets, Yahoo Finance for UK/Global
        factsheet_url = ""
        if item['class'] == 'Mutual Fund':
            isin = item.get('isin') or isin_map.get(item['name'])
            if isin:
                # Direct lookup on Groww using the ISIN number (extremely reliable and clean redirect)
                factsheet_url = f"https://groww.in/search?q={isin}"
            else:
                # Fallback search on Groww using fund name
                factsheet_url = f"https://groww.in/search?q={item['name'].replace(' ', '+')}"
        elif item['class'] == 'Equity' and item['geo'] == 'India':
            # Clean symbols (remove Yahoo NS suffix to match raw symbol formatting)
            symbol_raw = item['name'].split('.')[0]
            # Direct link to Trendlyne Stock page which is 100% stable
            factsheet_url = f"https://trendlyne.com/equity/{symbol_raw}/"
        elif item['class'] == 'Global ETF' or (item['class'] == 'Equity' and item['geo'] == 'UK'):
            factsheet_url = f"https://finance.yahoo.com/quote/{item['name']}"
        else:
            factsheet_url = f"https://finance.yahoo.com/quote/{item['name']}"

        processed.append({
            "Asset": item['name'],
            "Class": item['class'],
            "Geo": item['geo'],
            "Qty": item['qty'],
            "Avg Cost": item['avgCost'],
            "LTP": current_price,
            "Currency": item['currency'],
            "Invested (INR)": total_invested,
            "Current Value (INR)": current_val,
            "P&L (INR)": pnl,
            "P&L %": pnl_pct,
            "Source": item.get('source', 'Unknown'),
            "Cap Category": cap_cat,
            "Factsheet": factsheet_url
        })
    
    return pd.DataFrame(processed).sort_values(by="Current Value (INR)", ascending=False)


def process_timeline(cams_txns, t212_txns, fx_rate):
    """Aggregates chronological buy records to generate a cumulative deployed cash flow timeline."""
    records = []
    
    # 1. Parse CAMS deposits
    for tx in cams_txns:
        records.append({
            'date': tx['date'],
            'amount_inr': tx['amount_inr'],
            'source': 'CAMS'
        })
        
    # 2. Parse Trading 212 ISA cash transfers
    for tx in t212_txns:
        records.append({
            'date': tx['date'],
            'amount_inr': tx['amount_gbp'] * fx_rate,
            'source': 'Trading 212'
        })
        
    if not records:
        return pd.DataFrame()
        
    df = pd.DataFrame(records)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values(by='date')
    
    # Dynamic chronological cumsum grouped by source partitions
    df['Cumulative (INR)'] = df['amount_inr'].cumsum()
    df['Cumulative (GBP)'] = df['Cumulative (INR)'] / fx_rate
    
    return df

# ==========================================
# 6. SIDEBAR / CONFIGURATIONS & FILTERS
# ==========================================
st.sidebar.title("Portfolio Settings")

# 1. Global Display Currency Toggle
st.sidebar.subheader("Display Settings")
display_currency = st.sidebar.radio(
    "Preferred Currency",
    options=["INR", "GBP"],
    index=0,
    help="Toggle to view all portfolio metrics and ledgers converted into INR (₹) or GBP (£)."
)

# 2. Source Filters
st.sidebar.subheader("Filter Settings")
if st.session_state.df_portfolio is not None and not st.session_state.df_portfolio.empty:
    all_sources = sorted(st.session_state.df_portfolio["Source"].unique().tolist())
else:
    all_sources = ["Zerodha", "CAMS", "Trading 212"]

selected_sources = st.sidebar.multiselect(
    "Filter by Source",
    options=all_sources,
    default=all_sources,
    help="Select or deselect holding entities to refine calculations."
)

st.sidebar.markdown("---")

st.sidebar.subheader("Upload Holdings Statements")
zerodha_file = st.sidebar.file_uploader("Zerodha Equity (CSV or XLSX)", type=['csv', 'xlsx', 'xls'])
cams_file = st.sidebar.file_uploader("CAMS Mutual Funds (XLSX or CSV)", type=['xlsx', 'xls', 'csv'])
t212_file = st.sidebar.file_uploader("Trading 212 (CSV)", type=['csv'])

st.sidebar.markdown("---")
run_analysis = st.sidebar.button("Run Dashboard Analysis", use_container_width=True, type="primary")

# Execute compilation when user triggers the run
if run_analysis:
    with st.spinner("Processing statements and fetching live valuations..."):
        fx = get_exchange_rate()
        st.session_state.fx_rate = fx
        
        raw_holdings = []
        cams_txns = []
        t212_txns = []
        
        if zerodha_file:
            raw_holdings.extend(parse_zerodha_file(zerodha_file))
        if cams_file:
            raw_holdings.extend(parse_cams_excel(cams_file))
            # Collect transaction detail list for timeline progression
            cams_txns.extend(parse_cams_details_for_timeline(cams_file))
        if t212_file:
            raw_holdings.extend(parse_trading212_file(t212_file))
            t212_txns.extend(parse_trading212_txns_for_timeline(t212_file))
        
        if raw_holdings:
            st.session_state.df_portfolio = process_holdings(raw_holdings, fx)
        else:
            st.session_state.df_portfolio = pd.DataFrame()
            
        # Parse timeline datasets chronologically
        if cams_txns or t212_txns:
            st.session_state.df_timeline = process_timeline(cams_txns, t212_txns, fx)
        else:
            st.session_state.df_timeline = pd.DataFrame()

# ==========================================
# 7. MAIN PANEL VIEWPORT (Kite Blue Theme)
# ==========================================
st.title("Global Portfolio Analyst Dashboard")

if st.session_state.df_portfolio is None:
    # Onboarding Empty State Landing - Minimal and Clean, No Image
    st.markdown("""
    <div class="onboarding-card">
        <h3 style="margin-top:0; color:#387ed1; font-weight:600;">Welcome to your global cross-border investment workspace</h3>
        <p style="color:#555; font-size:14px; margin-bottom:15px;">
            Consolidate and track multi-currency asset values across Indian and UK platforms dynamically with real-time exchange rates and stock prices.
        </p>
        <h4 style="margin-top:20px; font-weight:500; color:#333; font-size:14px;">How to generate your unified metrics:</h4>
        <ol style="color:#555; font-size:14px; padding-left:20px; line-height:1.6;">
            <li>Upload your holding statements in the <b>left sidebar</b> (Zerodha CSV/XLSX, CAMS Mutual Funds XLSX/CSV, or Trading 212 CSV).</li>
            <li>Click the blue <b>'Run Dashboard Analysis'</b> button to build your report.</li>
        </ol>
    </div>
    """, unsafe_allow_html=True)

elif st.session_state.df_portfolio.empty:
    st.warning("No valid transaction or holdings data extracted. Please check your uploaded statement files and click 'Run Dashboard Analysis' again.")

else:
    # Filter working data based on selected sources
    df_portfolio = st.session_state.df_portfolio
    df_filtered = df_portfolio[df_portfolio["Source"].isin(selected_sources)]
    
    if df_filtered.empty:
        st.warning("Please select at least one source in the sidebar filter to show portfolio figures.")
    else:
        fx_rate = st.session_state.fx_rate
        
        # Decide conversion rates and currency signs dynamically
        rate_divisor = fx_rate if display_currency == "GBP" else 1.0
        currency_symbol = "£" if display_currency == "GBP" else "₹"
        
        # Financial metrics aggregated with no decimal places
        total_invested = round(df_filtered['Invested (INR)'].sum() / rate_divisor)
        current_value = round(df_filtered['Current Value (INR)'].sum() / rate_divisor)
        total_pnl = current_value - total_invested
        total_pnl_pct = round((total_pnl / total_invested) * 100) if total_invested > 0 else 0
        
        # Formatted Metric outputs (Accounting Style for P&L, no decimals)
        invested_str = f"{currency_symbol}{total_invested:,}"
        current_str = f"{currency_symbol}{current_value:,}"
        
        if total_pnl >= 0:
            pnl_str = f"{currency_symbol}{total_pnl:,}"
            pnl_pct_str = f"+{total_pnl_pct}%"
        else:
            pnl_str = f"({currency_symbol}{abs(total_pnl):,})"
            pnl_pct_str = f"({abs(total_pnl_pct)}%)"

        st.write(f"Interbank Spot Rate: 1 GBP = ₹{fx_rate:.2f} | Consolidated Base Currency: **{display_currency}**")
        
        # Summary Metrics Row
        m1, m2, m3 = st.columns(3)
        m1.metric("Current Portfolio Value", current_str)
        m2.metric("Total Capital Deployed", invested_str)
        m3.metric("Total Unrealized P&L", pnl_str, pnl_pct_str, delta_color="normal" if total_pnl >= 0 else "inverse")

        st.markdown("---")
        
        # Visual Layout (Allocation Pie Charts using clean corporate blues)
        c1, c2, c3 = st.columns(3)
        
        # Consistent Zerodha Blue Palette representation
        corporate_palette = ['#387ed1', '#244e8a', '#1d3557', '#457b9d', '#a8dadc']
        
        with c1:
            st.subheader("Allocation by Asset Class")
            fig_class = px.pie(df_filtered, values='Current Value (INR)', names='Class', hole=0.45,
                               color_discrete_sequence=corporate_palette)
            fig_class.update_traces(textposition='inside', textinfo='percent+label')
            fig_class.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig_class, use_container_width=True)

        with c2:
            st.subheader("Allocation by Cap / Category")
            fig_cap = px.pie(df_filtered, values='Current Value (INR)', names='Cap Category', hole=0.45,
                             color_discrete_sequence=['#1d3557', '#387ed1', '#457b9d', '#244e8a', '#9cb4cc', '#e2e8f0'])
            fig_cap.update_traces(textposition='inside', textinfo='percent+label')
            fig_cap.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig_cap, use_container_width=True)

        with c3:
            st.subheader("Allocation by Geography")
            fig_geo = px.pie(df_filtered, values='Current Value (INR)', names='Geo', hole=0.45,
                             color_discrete_sequence=['#387ed1', '#9cb4cc'])
            fig_geo.update_traces(textposition='inside', textinfo='percent+label')
            fig_geo.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig_geo, use_container_width=True)

        st.markdown("---")
        
        # Historical Capital Deployment Timeline Chart
        df_timeline = st.session_state.df_timeline
        if df_timeline is not None and not df_timeline.empty:
            st.subheader("Historical Capital Deployment Timeline")
            
            # Select correct display metrics column dynamically
            timeline_y = f"Cumulative ({display_currency})"
            
            fig_timeline = px.area(
                df_timeline,
                x='date',
                y=timeline_y,
                color='source',
                labels={timeline_y: f"Capital Deployed ({display_currency})", 'date': 'Timeline'},
                color_discrete_sequence=['#387ed1', '#244e8a', '#1d3557']
            )
            fig_timeline.update_layout(
                hovermode='x unified',
                xaxis_title="",
                yaxis_title=f"Cumulative Capital Deployed ({display_currency})",
                legend_title="Investment Source",
                margin=dict(t=10, b=10, l=10, r=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_timeline, use_container_width=True)
            st.markdown("---")

        st.subheader("Detailed Position Ledger")
        
        # Pre-formatting displays into strings to ensure strictly rounded accounting values
        display_df = pd.DataFrame()
        display_df["Asset Name"] = df_filtered["Asset"]
        display_df["Asset Class"] = df_filtered["Class"]
        display_df["Category / Cap"] = df_filtered["Cap Category"]
        display_df["Geography"] = df_filtered["Geo"]
        display_df["Source"] = df_filtered["Source"]
        
        # Request: Limit Qty to 0 decimals
        display_df["Qty"] = df_filtered["Qty"].apply(lambda x: f"{round(x):,}")
        
        # Pricing with local currency symbols for accuracy (retains 2 decimals where standard)
        def format_cost_ltp(row, col):
            curr_symbol = "£" if row["Currency"] == "GBP" else "₹"
            return f"{curr_symbol}{row[col]:,.2f}" if row[col] % 1 != 0 else f"{curr_symbol}{row[col]:,.0f}"

        display_df["Avg Purchase Cost"] = df_filtered.apply(lambda r: format_cost_ltp(r, "Avg Cost"), axis=1)
        display_df["LTP (Current)"] = df_filtered.apply(lambda r: format_cost_ltp(r, "LTP"), axis=1)
        
        # Dynamic Converted Value representations (rounded to integers)
        inv_col = f"Invested Value ({display_currency})"
        cur_col = f"Current Value ({display_currency})"
        pnl_col = f"P&L ({display_currency})"
        
        display_df[inv_col] = (df_filtered["Invested (INR)"] / rate_divisor).apply(lambda x: f"{currency_symbol}{round(x):,}")
        display_df[cur_col] = (df_filtered["Current Value (INR)"] / rate_divisor).apply(lambda x: f"{currency_symbol}{round(x):,}")
        
        # Accounting format logic for row columns
        def format_row_pnl(val):
            conv_val = val / rate_divisor
            rounded = round(conv_val)
            if rounded < 0:
                return f"({currency_symbol}{abs(rounded):,})"
            elif rounded > 0:
                return f"{currency_symbol}{rounded:,}"
            return f"{currency_symbol}0"
            
        def format_row_pct(val):
            rounded = round(val)
            if rounded < 0:
                return f"({abs(rounded)}%)"
            elif rounded > 0:
                return f"+{rounded}%"
            return "0%"

        display_df[pnl_col] = df_filtered["P&L (INR)"].apply(format_row_pnl)
        display_df["P&L %"] = df_filtered["P&L %"].apply(format_row_pct)
        display_df["Factsheet"] = df_filtered["Factsheet"]
        
        # Dynamic numerical column subset for alignment configurations
        numerical_cols = ["Qty", "Avg Purchase Cost", "LTP (Current)", inv_col, cur_col, pnl_col, "P&L %"]

        # Apply standard accounting colors and clean text-centering dynamically to all numerical columns
        def highlight_pnl(row):
            val = df_filtered.loc[row.name, "P&L (INR)"]
            color = 'color: #10b981; font-weight: bold;' if val > 0 else ('color: #ef4444; font-weight: bold;' if val < 0 else 'color: #6b7280;')
            return [color if col in [pnl_col, "P&L %"] else "" for col in row.index]

        styled_df = display_df.style.apply(highlight_pnl, axis=1).set_properties(
            **{'text-align': 'center'},
            subset=numerical_cols
        )
        
        st.dataframe(
            styled_df, 
            use_container_width=True, 
            hide_index=True,
            column_config={
                "Factsheet": st.column_config.LinkColumn(
                    "Factsheet",
                    help="Click to open the fund factsheet or asset analysis on Trendlyne / Yahoo Finance",
                    validate="^http",
                    display_text="View Factsheet ↗"
                )
            }
        )
