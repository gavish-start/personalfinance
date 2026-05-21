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
# 3. RAW STATEMENT PARSERS (Robust CSV, Excel & PDF)
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
                'currency': 'INR',
                'source': 'CAMS'
            })
        return holdings
    except Exception as e:
        st.error(f"Error parsing CAMS JSON statement: {e}")
        return []

def parse_cams_pdf(uploaded_file, password=""):
    try:
        import pypdf
    except ImportError:
        st.error("Please add 'pypdf' to your requirements.txt on GitHub to support CAMS PDF file uploads.")
        return []
    
    try:
        # Reset memory pointer and build isolated buffer
        uploaded_file.seek(0)
        file_bytes = io.BytesIO(uploaded_file.read())
        reader = pypdf.PdfReader(file_bytes)
        
        if reader.is_encrypted:
            if password:
                # Attempt with stripped password first, then fallback to original raw password
                decrypt_result = reader.decrypt(password.strip())
                if decrypt_result == 0:
                    decrypt_result = reader.decrypt(password)
                
                # Check for absolute encryption failure
                if decrypt_result == 0:
                    st.error("Authentication failed: Incorrect password for CAMS PDF. Usually, this is your PAN in UPPERCASE or your email.")
                    return []
            else:
                st.warning("This CAMS PDF statement is encrypted. Please enter the password in the sidebar.")
                return []
                
        full_text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                full_text += page_text + "\n"
                
        lines = [line.strip() for line in full_text.split('\n') if line.strip()]
        fund_keywords = re.compile(r'(?:Mutual\s+Fund|Fund|Growth|ELSS|Tax\s+Saver|Balanced|Equity|Debt|Liquid|Scheme|Option|Pln|Plan|Gr|Direct|Regular)', re.IGNORECASE)
        ignore_keywords = re.compile(r'(?:Statement|CAS|Summary|Page|CAMS|Date|Transaction|Folio\s+No|PAN|KYC|Registrar|Note|Report|Investor)', re.IGNORECASE)
        
        holdings = []
        current_scheme = None
        
        for i, line in enumerate(lines):
            # Cleanly capture potential mutual fund scheme name
            if fund_keywords.search(line) and not ignore_keywords.search(line) and 8 < len(line) < 100:
                cleaned_line = re.sub(r'^(?:Scheme|Fund|Name|Asset|Description)\s*[:\-]\s*', '', line, flags=re.IGNORECASE).strip()
                current_scheme = cleaned_line
                
            qty = None
            # Scan for explicit closing unit balances
            balance_match = re.search(r'(?:Closing\s+Unit\s+Balance|Closing\s+Balance|Balance\s+Units|Balance|Units)\s*[:\-]?\s*([\d,]+\.\d{2,4})', line, re.IGNORECASE)
            
            if balance_match:
                try:
                    qty = float(balance_match.group(1).replace(',', ''))
                except ValueError:
                    pass
            else:
                # Fallback: scan multi-line table layouts where quantity details sit directly on subsequent rows
                balance_keyword_match = re.search(r'(?:Closing\s+Unit\s+Balance|Closing\s+Balance|Balance\s+Units|Balance\s+Value|Units|Balance)', line, re.IGNORECASE)
                if balance_keyword_match and current_scheme:
                    num_match = re.search(r'([\d,]+\.\d{2,4})', line)
                    if num_match:
                        try:
                            qty = float(num_match.group(1).replace(',', ''))
                        except ValueError:
                            pass
                    elif i < len(lines) - 1:
                        num_match = re.search(r'([\d,]+\.\d{2,4})', lines[i+1])
                        if num_match:
                            try:
                                qty = float(num_match.group(1).replace(',', ''))
                            except ValueError:
                                pass
            
            if qty is not None and qty > 0 and current_scheme:
                # Seek current NAV in immediate line neighborhood
                nav = 0.0
                nav_match = re.search(r'(?:NAV|Price|Rate)\s*[:\-]?\s*(?:Rs\.?)?\s*([\d,]+\.\d+)', line, re.IGNORECASE)
                if not nav_match and i < len(lines) - 1:
                    nav_match = re.search(r'(?:NAV|Price|Rate)\s*[:\-]?\s*(?:Rs\.?)?\s*([\d,]+\.\d+)', lines[i+1], re.IGNORECASE)
                    
                if nav_match:
                    try:
                        nav = float(nav_match.group(1).replace(',', ''))
                    except ValueError:
                        pass
                
                # Seek associated Cost Value to build average purchase cost heuristics
                cost_val = None
                cost_match = re.search(r'(?:Cost\s+Value|Cost|Invested\s+Value|Amount\s+Invested|Cost\s+Value\s*\(Rs\))\s*[:\-]?\s*(?:Rs\.?)?\s*([\d,]+\.\d+)', line, re.IGNORECASE)
                if not cost_match and i < len(lines) - 1:
                    cost_match = re.search(r'(?:Cost\s+Value|Cost|Invested\s+Value|Amount\s+Invested|Cost\s+Value\s*\(Rs\))\s*[:\-]?\s*(?:Rs\.?)?\s*([\d,]+\.\d+)', lines[i+1], re.IGNORECASE)
                
                if cost_match:
                    try:
                        cost_val = float(cost_match.group(1).replace(',', ''))
                    except ValueError:
                        pass
                
                avg_cost = cost_val / qty if cost_val is not None and qty > 0 else (nav if nav > 0 else 0.0)
                current_price = nav if nav > 0 else avg_cost
                
                if not any(h['name'] == current_scheme for h in holdings):
                    holdings.append({
                        'name': current_scheme,
                        'class': 'Mutual Fund',
                        'geo': 'India',
                        'qty': qty,
                        'avgCost': avg_cost,
                        'currentPrice': current_price,
                        'currency': 'INR',
                        'source': 'CAMS'
                    })
                current_scheme = None
                qty = None
                        
        return holdings
    except Exception as e:
        st.error(f"Error reading CAMS PDF file: {e}")
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
            "P&L %": pnl_pct,
            "Source": item.get('source', 'Unknown')
        })
    
    return pd.DataFrame(processed).sort_values(by="Current Value (INR)", ascending=False)

# ==========================================
# 5. SIDEBAR / CONFIGURATIONS & FILTERS
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
cams_file = st.sidebar.file_uploader("CAMS Mutual Funds (JSON or PDF)", type=['json', 'pdf'])

cams_password = ""
if cams_file and cams_file.name.endswith('.pdf'):
    cams_password = st.sidebar.text_input("CAMS PDF Password", type="password", help="Enter PAN (UPPERCASE) or your registered email address.")

t212_file = st.sidebar.file_uploader("Trading 212 (CSV)", type=['csv'])

use_demo = st.sidebar.checkbox("Use Demo Data", value=False)

# Demo raw holdings fallback with explicit sources mapped
demo_raw_holdings = [
    { 'name': 'ASIANPAINT', 'class': 'Equity', 'geo': 'India', 'qty': 15, 'avgCost': 2583.93, 'currentPrice': 2600.70, 'currency': 'INR', 'source': 'Zerodha' },
    { 'name': 'BAJFINANCE', 'class': 'Equity', 'geo': 'India', 'qty': 70, 'avgCost': 629.85, 'currentPrice': 923.55, 'currency': 'INR', 'source': 'Zerodha' },
    { 'name': 'DMART', 'class': 'Equity', 'geo': 'India', 'qty': 17, 'avgCost': 3305.23, 'currentPrice': 4236.00, 'currency': 'INR', 'source': 'Zerodha' },
    { 'name': 'GRANULES', 'class': 'Equity', 'geo': 'India', 'qty': 30, 'avgCost': 345.63, 'currentPrice': 766.45, 'currency': 'INR', 'source': 'Zerodha' },
    { 'name': 'HCLTECH', 'class': 'Equity', 'geo': 'India', 'qty': 10, 'avgCost': 1100.00, 'currentPrice': 1350.50, 'currency': 'INR', 'source': 'Zerodha' },
    { 'name': 'HDFCBANK', 'class': 'Equity', 'geo': 'India', 'qty': 50, 'avgCost': 1450.00, 'currentPrice': 1520.30, 'currency': 'INR', 'source': 'Zerodha' },
    { 'name': 'IDFCFIRSTB', 'class': 'Equity', 'geo': 'India', 'qty': 200, 'avgCost': 75.50, 'currentPrice': 82.10, 'currency': 'INR', 'source': 'Zerodha' },
    { 'name': 'SBI Small Cap Fund Reg Growth', 'class': 'Mutual Fund', 'geo': 'India', 'qty': 4217.94, 'avgCost': 59.56, 'currentPrice': 164.51, 'currency': 'INR', 'source': 'CAMS' },
    { 'name': 'Kotak Focused Fund Gr', 'class': 'Mutual Fund', 'geo': 'India', 'qty': 6350.46, 'avgCost': 16.59, 'currentPrice': 25.73, 'currency': 'INR', 'source': 'CAMS' },
    { 'name': 'Kotak ELSS Tax Saver Fund - Gr', 'class': 'Mutual Fund', 'geo': 'India', 'qty': 837.64, 'avgCost': 119.38, 'currentPrice': 110.08, 'currency': 'INR', 'source': 'CAMS' },
    { 'name': 'VWRP.L', 'class': 'Global ETF', 'geo': 'UK', 'qty': 3.3722, 'avgCost': 127.89, 'currentPrice': 131.20, 'currency': 'GBP', 'source': 'Trading 212' },
    { 'name': 'Uninvested Cash (ISA)', 'class': 'Cash', 'geo': 'UK', 'qty': 2568.70, 'avgCost': 1.00, 'currentPrice': 1.00, 'currency': 'GBP', 'source': 'Trading 212' }
]

st.sidebar.markdown("---")
run_analysis = st.sidebar.button("Run Dashboard Analysis", use_container_width=True, type="primary")

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
                if cams_file.name.endswith('.pdf'):
                    raw_holdings.extend(parse_cams_pdf(cams_file, cams_password))
                else:
                    raw_holdings.extend(parse_cams_file(cams_file))
            if t212_file:
                raw_holdings.extend(parse_trading212_file(t212_file))
        
        if raw_holdings:
            st.session_state.df_portfolio = process_holdings(raw_holdings, fx)
        else:
            st.session_state.df_portfolio = pd.DataFrame()

# ==========================================
# 6. MAIN PANEL VIEWPORT (Kite Blue Theme)
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
            <li>Upload your holding statements in the <b>left sidebar</b> (Zerodha CSV/XLSX, CAMS Mutual Funds JSON/PDF, or Trading 212 CSV).</li>
            <li>Alternatively, toggle the <b>'Use Demo Data'</b> checkbox in the sidebar to instantly inspect the interactive workspace.</li>
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
        c1, c2 = st.columns(2)
        
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
            st.subheader("Allocation by Geography")
            fig_geo = px.pie(df_filtered, values='Current Value (INR)', names='Geo', hole=0.45,
                             color_discrete_sequence=['#387ed1', '#9cb4cc'])
            fig_geo.update_traces(textposition='inside', textinfo='percent+label')
            fig_geo.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig_geo, use_container_width=True)

        st.markdown("---")
        st.subheader("Detailed Position Ledger")
        
        # Pre-formatting displays into strings to ensure strictly rounded accounting values
        display_df = pd.DataFrame()
        display_df["Asset Name"] = df_filtered["Asset"]
        display_df["Asset Class"] = df_filtered["Class"]
        display_df["Geography"] = df_filtered["Geo"]
        display_df["Source"] = df_filtered["Source"]
        
        # Request 4: Limit Qty to 0 decimals
        display_df["Qty"] = df_filtered["Qty"].apply(lambda x: f"{round(x):,}")
        
        # Pricing with local currency symbols for accuracy (retains 2 decimals where standard)
        def format_cost_ltp(row, col):
            curr_symbol = "£" if row["Currency"] == "GBP" else "₹"
            return f"{curr_symbol}{row[col]:,.2f}" if row[col] % 1 != 0 else f"{curr_symbol}{row[col]:,.0f}"

        display_df["Avg Purchase Cost"] = df_filtered.apply(lambda r: format_cost_ltp(r, "Avg Cost"), axis=1)
        display_df["LTP (Current)"] = df_filtered.apply(lambda r: format_cost_ltp(r, "LTP"), axis=1)
        
        # Dynamic Converted Value representations (rounded to integers per Request 4)
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
        
        st.dataframe(styled_df, use_container_width=True, hide_index=True)
