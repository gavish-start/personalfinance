import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import json
import plotly.express as px
import google.generativeai as genai

# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(page_title="Global Portfolio & AI", page_icon="🌍", layout="wide")

# ==========================================
# 1. MARKET DATA INTEGRATION (Cached for speed)
# ==========================================
@st.cache_data(ttl=3600) # Cache data for 1 hour to avoid hitting APIs too often
def get_exchange_rate():
    try:
        return yf.Ticker("GBPINR=X").fast_info['last_price']
    except:
        return 105.80 # Fallback

@st.cache_data(ttl=3600)
def get_live_stock_price(ticker_symbol):
    try:
        return yf.Ticker(ticker_symbol).fast_info['last_price']
    except:
        return None

@st.cache_data(ttl=3600)
def get_mutual_fund_nav(scheme_name):
    # Hardcoded mapping for demo. In a real DB, you'd map names to AMFI codes.
    code_map = {
        "Kotak Focused Fund Gr": "147473",
        "SBI Small Cap Fund Reg Growth": "119616",
        "Kotak ELSS Tax Saver Fund - Gr": "119773" 
    }
    scheme_code = code_map.get(scheme_name)
    if not scheme_code: return None
    try:
        response = requests.get(f"https://api.mfapi.in/mf/{scheme_code}").json()
        return float(response['data'][0]['nav'])
    except:
        return None

# ==========================================
# 2. DATA PROCESSING
# ==========================================
def process_holdings(raw_data, fx_rate):
    """Calculates invested value, current value, and P&L in base currency (INR)"""
    processed = []
    for item in raw_data:
        rate = fx_rate if item['currency'] == 'GBP' else 1
        
        # Determine current price (use live API if available, else fallback to mock)
        current_price = item['currentPrice']
        if item['class'] == 'Equity':
            live_price = get_live_stock_price(f"{item['name']}.NS")
            if live_price: current_price = live_price
        elif item['class'] == 'Mutual Fund':
            live_nav = get_mutual_fund_nav(item['name'])
            if live_nav: current_price = live_nav
        elif item['class'] == 'Global ETF':
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
            "Qty": round(item['qty'], 2),
            "Avg Cost": round(item['avgCost'], 2),
            "LTP": round(current_price, 2),
            "Currency": item['currency'],
            "Invested (INR)": round(total_invested, 2),
            "Current Value (INR)": round(current_val, 2),
            "P&L (INR)": round(pnl, 2),
            "P&L %": round(pnl_pct, 2)
        })
    
    return pd.DataFrame(processed).sort_values(by="Current Value (INR)", ascending=False)

# ==========================================
# 3. SIDEBAR & DATA LOADING
# ==========================================
st.sidebar.title("⚙️ Settings & Data")

# AI Setup
st.sidebar.subheader("🤖 AI Agent Setup")
api_key = st.sidebar.text_input("Gemini API Key (Optional)", type="password", help="Get a free key from Google AI Studio to enable the Chat Agent.")

st.sidebar.markdown("---")
st.sidebar.subheader("📄 Upload Statements")
zerodha_file = st.sidebar.file_uploader("Zerodha Equity (CSV)", type=['csv'])
cams_file = st.sidebar.file_uploader("CAMS Mutual Funds (JSON)", type=['json'])
t212_file = st.sidebar.file_uploader("Trading 212 (CSV)", type=['csv'])

use_demo = st.sidebar.checkbox("Use Demo Data", value=True)

# --- Fallback/Demo Data ---
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

fx_rate = get_exchange_rate()

if use_demo:
    df_portfolio = process_holdings(demo_raw_holdings, fx_rate)
else:
    # In a real scenario, you would parse the uploaded files here based on the logic 
    # we discussed earlier, appending to a `user_raw_holdings` array.
    # For now, we fallback to demo if files aren't perfectly mapped yet.
    st.sidebar.warning("File parsing module active. (Using demo data as placeholder).")
    df_portfolio = process_holdings(demo_raw_holdings, fx_rate)


# ==========================================
# 4. MAIN UI - TABS
# ==========================================
st.title("🌍 Global Portfolio Dashboard")
st.write(f"**Live Exchange Rate:** 1 GBP = ₹{fx_rate:.2f}")

tab1, tab2 = st.tabs(["📊 Dashboard & Analysis", "🤖 AI Portfolio Agent"])

# --- TAB 1: DASHBOARD ---
with tab1:
    # Summary Metrics
    total_invested = df_portfolio['Invested (INR)'].sum()
    current_value = df_portfolio['Current Value (INR)'].sum()
    total_pnl = current_value - total_invested
    total_pnl_pct = (total_pnl / total_invested) * 100 if total_invested > 0 else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Current Value (INR)", f"₹{current_value:,.0f}")
    col2.metric("Total Invested (INR)", f"₹{total_invested:,.0f}")
    col3.metric("Unrealized P&L", f"₹{total_pnl:,.0f}", f"{total_pnl_pct:.2f}%")
    
    # Charts
    st.markdown("---")
    c1, c2 = st.columns(2)
    
    with c1:
        st.subheader("Asset Class Allocation")
        fig_class = px.pie(df_portfolio, values='Current Value (INR)', names='Class', hole=0.4, 
                           color_discrete_sequence=px.colors.qualitative.Pastel)
        st.plotly_chart(fig_class, use_container_width=True)

    with c2:
        st.subheader("Geographical Exposure")
        fig_geo = px.pie(df_portfolio, values='Current Value (INR)', names='Geo', hole=0.4,
                         color_discrete_sequence=['#f97316', '#3b82f6'])
        st.plotly_chart(fig_geo, use_container_width=True)

    # Granular Table
    st.markdown("---")
    st.subheader("Detailed Holdings")
    
    # Apply some basic styling to the dataframe
    def color_pnl(val):
        color = 'green' if val > 0 else 'red'
        return f'color: {color}'

    styled_df = df_portfolio.style.map(color_pnl, subset=['P&L (INR)', 'P&L %']).format(
        {"Invested (INR)": "₹{:,.2f}", "Current Value (INR)": "₹{:,.2f}", "P&L %": "{:.2f}%"}
    )
    st.dataframe(styled_df, use_container_width=True, hide_index=True)


# --- TAB 2: AI CHAT AGENT ---
with tab2:
    st.header("Chat with your Portfolio")
    st.write("Ask questions about your asset allocation, performance, or get general market analysis.")
    
    if not api_key:
        st.warning("⚠️ Please enter your Gemini API Key in the sidebar to activate the AI Agent.")
    else:
        # Configure Gemini
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # Prepare the context (giving the AI your exact portfolio data)
        portfolio_context = df_portfolio.to_markdown(index=False)
        system_prompt = f"""
        You are a highly intelligent financial advisor. 
        Here is the user's current investment portfolio data (converted to INR for base comparison):
        
        {portfolio_context}
        
        Answer the user's questions accurately based ONLY on this data. Be concise, professional, and helpful.
        """

        # Initialize chat history in session state
        if "messages" not in st.session_state:
            st.session_state.messages = []

        # Display chat messages from history
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        # React to user input
        if prompt := st.chat_input("E.g., What is my highest performing asset?"):
            # Display user message
            st.chat_message("user").markdown(prompt)
            st.session_state.messages.append({"role": "user", "content": prompt})

            # Format the full prompt for the LLM
            full_prompt = system_prompt + f"\n\nUser Question: {prompt}"

            with st.chat_message("assistant"):
                with st.spinner("Analyzing portfolio..."):
                    try:
                        response = model.generate_content(full_prompt)
                        st.markdown(response.text)
                        st.session_state.messages.append({"role": "assistant", "content": response.text})
                    except Exception as e:
                        st.error(f"Error communicating with AI: {e}")
