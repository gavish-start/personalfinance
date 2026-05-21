import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import json
import plotly.express as px

# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(page_title="Global Portfolio Analyzer", page_icon="🌍", layout="wide")

# ==========================================
# 1. SESSION STATE INITIALIZATION
# ==========================================
if "df_portfolio" not in st.session_state:
    st.session_state.df_portfolio = None
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
    # Mapping table for Indian AMFI mutual fund codes
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
# 3. RAW STATEMENT PARSERS (Robust CSV & Excel)
# ==========================================
def parse_zerodha_file(uploaded_file):
    try:
        if uploaded_file.name.endswith('.xlsx') or uploaded_file.name.endswith('.xls'):
            # Read Excel raw to find header row
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
            # CSV processing
            lines = uploaded_file.getvalue().decode("utf-8").split("\n")
            header_idx = 0
            for i, line in enumerate(lines):
                if "Symbol" in line:
                    header_idx = i
                    break
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, skiprows=header_idx)
        
        # Clean columns safely converting each to string first
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
                'currency': 'INR'
            })
        return holdings
    except Exception as e:
        st.error(f"Error parsing Zerodha statement: {e}")
        return []

def parse_cams_file(uploaded_file):
    try:
        data = json.load(uploaded_file)
        holdings = []
        for item in data:
            qty = float(item.get('UnitBal', 0))
            cost_val = float(item.get('CostValue', 0))
            current_val = float(item.get('CurrentValue', 0))
            scheme_name = item.get('Scheme', 'Unknown Fund')
            
            avg_cost = cost_val / qty if qty > 0 else 0
            current_price = current_val / qty if qty > 0 else 0
            
            holdings.append({
                'name': scheme_name,
                'class': 'Mutual Fund',
                'geo': 'India',
                'qty': qty,
                'avgCost': avg_cost,
                'currentPrice': current_price,
                'currency': 'INR'
            })
        return holdings
    except Exception as e:
        st.error(f"Error parsing CAMS JSON statement: {e}")
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
                    'currency': 'GBP'
                })
        if cash > 0:
            holdings.append({
                'name': 'Uninvested Cash (ISA)',
                'class': 'Cash',
                'geo': 'UK',
                'qty': cash,
                'avgCost': 1.0,
                'currentPrice': 1.0,
                'currency': 'GBP'
            })
        return holdings
    except Exception as e:
        st.error(f"Error parsing Trading 212 statement: {e}")
        return []

# ==========================================
# 4. PROCESSING PIPELINE
# ==========================================
def process_holdings(raw_data, fx_rate):
    processed = []
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
            "P&L %": pnl_pct
        })
    
    return pd.DataFrame(processed).sort_values(by="Current Value (INR)", ascending=False)

# ==========================================
# 5. SIDEBAR / SETTINGS & UPLOADER
# ==========================================
st.sidebar.title("⚙️ Portfolio Configuration")

st.sidebar.subheader("📄 Upload Holdings Statements")
zerodha_file = st.sidebar.file_uploader("Zerodha Equity (CSV or XLSX)", type=['csv', 'xlsx', 'xls'])
cams_file = st.sidebar.file_uploader("CAMS Mutual Funds (JSON)", type=['json'])
t212_file = st.sidebar.file_uploader("Trading 212 (CSV)", type=['csv'])

use_demo = st.sidebar.checkbox("Use Demo Data", value=False)

# Demo raw holdings fallback
demo_raw_holdings = [
    { 'name': 'ASIANPAINT', 'class': 'Equity', 'geo': 'India', 'qty': 15, 'avgCost': 2583.93, 'currentPrice': 2600.70, 'currency': 'INR' },
    { 'name': 'BAJFINANCE', 'class': 'Equity', 'geo': 'India', 'qty': 70, 'avgCost': 629.85, 'currentPrice': 923.55, 'currency': 'INR' },
    { 'name': 'DMART', 'class': 'Equity', 'geo': 'India', 'qty': 17, 'avgCost': 3305.23, 'currentPrice': 4236.00, 'currency': 'INR' },
    { 'name': 'GRANULES', 'class': 'Equity', 'geo': 'India', 'qty': 30, 'avgCost': 345.63, 'currentPrice': 766.45, 'currency': 'INR' },
    { 'name': 'HCLTECH', 'class': 'Equity', 'geo': 'India', 'qty': 10, 'avgCost': 1100.00, 'currentPrice': 1350.50, 'currency': 'INR' },
    { 'name': 'HDFCBANK', 'class': 'Equity', 'geo': 'India', 'qty': 50, 'avgCost': 1450.00, 'currentPrice': 1520.30, 'currency': 'INR' },
    { 'name': 'IDFCFIRSTB', 'class': 'Equity', 'geo': 'India', 'qty': 200, 'avgCost': 75.50, 'currentPrice': 82.10, 'currency': 'INR' },
    { 'name': 'SBI Small Cap Fund Reg Growth', 'class': 'Mutual Fund', 'geo': 'India', 'qty': 4217.94, 'avgCost': 59.56, 'currentPrice': 164.51, 'currency': 'INR' },
    { 'name': 'Kotak Focused Fund Gr', 'class': 'Mutual Fund', 'geo': 'India', 'qty': 6350.46, 'avgCost': 16.59, 'currentPrice': 25.73, 'currency': 'INR' },
    { 'name': 'Kotak ELSS Tax Saver Fund - Gr', 'class': 'Mutual Fund', 'geo': 'India', 'qty': 837.64, 'avgCost': 119.38, 'currentPrice': 110.08, 'currency': 'INR' },
    { 'name': 'VWRP.L', 'class': 'Global ETF', 'geo': 'UK', 'qty': 3.3722, 'avgCost': 127.89, 'currentPrice': 131.20, 'currency': 'GBP' },
    { 'name': 'Uninvested Cash (ISA)', 'class': 'Cash', 'geo': 'UK', 'qty': 2568.70, 'avgCost': 1.00, 'currentPrice': 1.00, 'currency': 'GBP' }
]

st.sidebar.markdown("---")
run_analysis = st.sidebar.button("📊 Run Dashboard Analysis", use_container_width=True, type="primary")

# Execute compilation when user triggers the run
if run_analysis:
    with st.spinner("Processing statements and fetching live valuations..."):
        fx = get_exchange_rate()
        st.session_state.fx_rate = fx
        
        raw_holdings = []
        if use_demo:
            raw_holdings = demo_raw_holdings
        else:
            if zerodha_file:
                raw_holdings.extend(parse_zerodha_file(zerodha_file))
            if cams_file:
                raw_holdings.extend(parse_cams_file(cams_file))
            if t212_file:
                raw_holdings.extend(parse_trading212_file(t212_file))
        
        if raw_holdings:
            st.session_state.df_portfolio = process_holdings(raw_holdings, fx)
        else:
            st.session_state.df_portfolio = pd.DataFrame()

# ==========================================
# 6. MAIN PANEL VIEWPORT
# ==========================================
st.title("🌍 Global Portfolio Analyst Dashboard")

if st.session_state.df_portfolio is None:
    # Onboarding Empty State Landing
    st.info("👋 **Welcome to your global cross-border investment workspace!**")
    st.markdown("""
    To generate your unified metrics:
    1. Upload your statements on the **left sidebar** (Zerodha `.csv`/`.xlsx`, CAMS Mutual Funds `.json`, or Trading 212 `.csv`).
    2. Alternatively, check the **'Use Demo Data'** checkbox to instantly see the ecosystem in action.
    3. Click the red **'Run Dashboard Analysis'** button to build the report.
    """)
    st.image("https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?auto=format&fit=crop&w=1200&q=80", caption="Consolidate multi-currency asset values dynamically.", use_container_width=True)

elif st.session_state.df_portfolio.empty:
    st.warning("⚠️ No valid transaction or holdings data extracted. Please check your uploaded statement files and click 'Run Dashboard Analysis' again.")

else:
    df_portfolio = st.session_state.df_portfolio
    fx_rate = st.session_state.fx_rate
    
    # Financial metrics aggregated with no decimal places
    total_invested = round(df_portfolio['Invested (INR)'].sum())
    current_value = round(df_portfolio['Current Value (INR)'].sum())
    total_pnl = current_value - total_invested
    total_pnl_pct = round((total_pnl / total_invested) * 100) if total_invested > 0 else 0
    
    # Formatted Metric outputs (Accounting Style for P&L)
    invested_str = f"₹{total_invested:,}"
    current_str = f"₹{current_value:,}"
    
    if total_pnl >= 0:
        pnl_str = f"₹{total_pnl:,}"
        pnl_pct_str = f"+{total_pnl_pct}%"
    else:
        pnl_str = f"(₹{abs(total_pnl):,})"
        pnl_pct_str = f"({abs(total_pnl_pct)}%)"

    st.write(f"**Interbank Spot Rate:** 1 GBP = ₹{fx_rate:.2f} | Consolidated Base Currency: **INR (₹)**")
    
    # Summary Metrics Row
    m1, m2, m3 = st.columns(3)
    m1.metric("Current Portfolio Value", current_str)
    m2.metric("Total Capital Deployed", invested_str)
    m3.metric("Total Unrealized P&L", pnl_str, pnl_pct_str, delta_color="normal" if total_pnl >= 0 else "inverse")

    st.markdown("---")
    
    # Visual Layout (Allocation Pie Charts)
    c1, c2 = st.columns(2)
    
    with c1:
        st.subheader("💼 Allocation by Asset Class")
        fig_class = px.pie(df_portfolio, values='Current Value (INR)', names='Class', hole=0.45,
                           color_discrete_sequence=px.colors.qualitative.Safe)
        fig_class.update_traces(textposition='inside', textinfo='percent+label')
        fig_class.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig_class, use_container_width=True)

    with c2:
        st.subheader("🗺️ Allocation by Geography")
        fig_geo = px.pie(df_portfolio, values='Current Value (INR)', names='Geo', hole=0.45,
                         color_discrete_sequence=['#4338ca', '#f97316'])
        fig_geo.update_traces(textposition='inside', textinfo='percent+label')
        fig_geo.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig_geo, use_container_width=True)

    st.markdown("---")
    st.subheader("🔍 Detailed Position Ledger")
    
    # Pre-formatting displays into strings to ensure strictly rounded accounting values
    display_df = pd.DataFrame()
    display_df["Asset Name"] = df_portfolio["Asset"]
    display_df["Asset Class"] = df_portfolio["Class"]
    display_df["Geography"] = df_portfolio["Geo"]
    display_df["Qty"] = df_portfolio["Qty"].apply(lambda x: f"{x:,.2f}" if x % 1 != 0 else f"{x:,.0f}")
    
    # Pricing with currency symbols
    def format_cost_ltp(row, col):
        curr_symbol = "£" if row["Currency"] == "GBP" else "₹"
        return f"{curr_symbol}{row[col]:,.2f}" if row[col] % 1 != 0 else f"{curr_symbol}{row[col]:,.0f}"

    display_df["Avg Purchase Cost"] = df_portfolio.apply(lambda r: format_cost_ltp(r, "Avg Cost"), axis=1)
    display_df["LTP (Current)"] = df_portfolio.apply(lambda r: format_cost_ltp(r, "LTP"), axis=1)
    
    # Invested & Current Rounded integers
    display_df["Invested Value (INR)"] = df_portfolio["Invested (INR)"].apply(lambda x: f"₹{round(x):,}")
    display_df["Current Value (INR)"] = df_portfolio["Current Value (INR)"].apply(lambda x: f"₹{round(x):,}")
    
    # Accounting Format for P&L columns
    def format_accounting_inr(val):
        rounded = round(val)
        if rounded < 0:
            return f"(₹{abs(rounded):,})"
        elif rounded > 0:
            return f"₹{rounded:,}"
        return "₹0"
        
    def format_accounting_pct(val):
        rounded = round(val)
        if rounded < 0:
            return f"({abs(rounded)}%)"
        elif rounded > 0:
            return f"+{rounded}%"
        return "0%"

    display_df["P&L (INR)"] = df_portfolio["P&L (INR)"].apply(format_accounting_inr)
    display_df["P&L %"] = df_portfolio["P&L %"].apply(format_accounting_pct)
    
    # Apply standard accounting colors to table rows
    def highlight_pnl(row):
        val = df_portfolio.loc[row.name, "P&L (INR)"]
        color = 'color: #10b981; font-weight: bold;' if val > 0 else ('color: #ef4444; font-weight: bold;' if val < 0 else 'color: #6b7280;')
        return [color if col in ["P&L (INR)", "P&L %"] else "" for col in row.index]

    styled_df = display_df.style.apply(highlight_pnl, axis=1)
    st.dataframe(styled_df, use_container_width=True, hide_index=True)
