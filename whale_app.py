import streamlit as st
import pandas as pd
import yfinance as yf
import re

import os
from pathlib import Path

# Create a local writable cache folder for yfinance
CACHE_DIR = "./.cache"
Path(CACHE_DIR).mkdir(exist_ok=True)
os.environ["YFINANCE_CACHE_DIR"] = CACHE_DIR

# --- ⚙️ PAGE SETUP ---
st.set_page_config(page_title="Whale Sniper", page_icon="🐋", layout="wide")
st.title("💰 WHALE TRACKER DASHBOARD")

# --- GLOBAL LOOKUP ---
DELIVERY_LOOKUP = {}

# --- HELPER FUNCTIONS ---
def get_daily_deals(bulk_file, block_file):
    """Reads uploaded CSVs directly from memory and cleans headers."""
    dfs = []
    try:
        if bulk_file:
            df_bulk = pd.read_csv(bulk_file, skipinitialspace=True)
            df_bulk.columns = [re.sub(r'[\"\n\t\xa0]', '', str(c)).strip().upper() for c in df_bulk.columns]
            dfs.append(df_bulk)
            
        if block_file:
            df_block = pd.read_csv(block_file, skipinitialspace=True)
            df_block.columns = [re.sub(r'[\"\n\t\xa0]', '', str(c)).strip().upper() for c in df_block.columns]
            dfs.append(df_block)
            
        if not dfs:
            return pd.DataFrame()

        deals = pd.concat(dfs, ignore_index=True)

        col_map = {
            'SYMBOL': 'Symbol',
            'QUANTITY TRADED': 'Quantity',
            'TRADE PRICE/ WEIGHTED. AVG. PRICE': 'TradePrice',
            'BUY/SELL': 'Buy / Sell'
        }
        deals.rename(columns=col_map, inplace=True)

        for col in ['Quantity', 'TradePrice']:
            if col in deals.columns:
                deals[col] = pd.to_numeric(deals[col].astype(str).str.replace(',', ''), errors='coerce')

        deals = deals.dropna(subset=['Quantity', 'TradePrice', 'Symbol'])
        return deals
    except Exception as e:
        st.error(f"❌ Error during cleaning: {e}")
        return pd.DataFrame()

def load_delivery_data(file_obj):
    if not file_obj:
        return {}
    try:
        column_names = [f"Col_{i}" for i in range(7)]
        
        # FIX 1: Tell pandas to treat Col_0 and Col_2 as strings immediately
        df = pd.read_csv(
            file_obj, 
            skiprows=4,          
            names=column_names,  
            header=None,
            dtype={f"Col_{i}": str for i in range(7)}, # Force EVERYTHING to string first
            on_bad_lines='skip'  
        )
        
        # FIX 2: Now we can safely strip and filter without any dtype warnings
        df['Col_0'] = df['Col_0'].str.strip()
        temppf = df[df['Col_0'] == "20"].copy()
        
        if temppf.empty:
            return {}

        # FIX 3: Clean the Symbol column (Col_2) and the Delivery column (Col_6)
        delivery_map = pd.Series(
            temppf['Col_6'].values, 
            index=temppf['Col_2'].str.strip()
        ).to_dict()
        
        return delivery_map
    except Exception as e:
        st.error(f"❌ Error parsing DAT file: {e}")
        return {}

def get_advanced_metrics(symbol):
    try:
        # 1. Clean the symbol (Remove -EQ, -BE etc. which NSE files often have)
        # This ensures 'RELIANCE-EQ' becomes 'RELIANCE' before adding '.NS'
        clean_symbol = str(symbol).split('-')[0].strip()
        
        delivery_pct = DELIVERY_LOOKUP.get(symbol, 0)
        
        # 2. Use a Session with a User-Agent to prevent Yahoo from blocking the Cloud IP
        import requests
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        
        ticker = yf.Ticker(f"{clean_symbol}.NS", session=session)
        
        # 3. Use fast_info or get_info (fast_info is more reliable in 2026 for prices)
        info = ticker.info
        
        # If .info fails, try fetching just the price to see if it's alive
        cmp = info.get("currentPrice") or info.get("regularMarketPrice") or 0
        
        if cmp == 0:
            return None # Skip if we can't even get a price

        return {
            "Shares_Outstanding": info.get("sharesOutstanding", 0),
            "CMP": cmp,
            "Delivery_Pct": delivery_pct,
            "Market_Cap": info.get("marketCap", 0),
            "Debt": info.get("totalDebt", 0),
            "Sector": info.get("sector", "Unknown"),
            "Industry": info.get("industry", "Unknown") 
        }
    except Exception as e:
        return None

def check_shariah(details):
    if not details: return "⚠️ Missing Data"
    status = "✅ YES"
    sin_sectors = ['Bank', 'Financial', 'Alcohol', 'Breweries', 'Gambling', 'Defense', 'Tobacco']
    sector = str(details.get('Sector', '')).strip()
    industry = str(details.get('Industry', '')).strip()
    
    for sin in sin_sectors:
        if sin.lower() in sector.lower() or sin.lower() in industry.lower():
            return "❌ NO (Sector)"

    market_cap = details.get('Market_Cap', 0)
    total_debt = details.get('Debt', 0)
    if market_cap > 0:
        debt_ratio = total_debt / market_cap
        if debt_ratio > 0.33:
            return f"❌ NO (Debt {round(debt_ratio*100, 1)}%)"
    return status

# --- 🎛️ STREAMLIT SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Strategy Settings")
    MIN_EQUITY_PERCENT = st.number_input("Min Equity % (Rule A)", min_value=0.0, max_value=100.0, value=0.1, step=0.1)
    MIN_DELIVERY_PERCENT = st.number_input("Min Delivery % (Rule B)", min_value=0, max_value=100, value=40, step=1)
    STRICT_PRICE_SUPPORT = st.checkbox("Strict Price Support (CMP < Avg Entry)", value=True)
    
    st.divider()
    
# --- 🔗 NSE DATA SOURCE LINKS ---
    st.header("📥 Data Sources")
    st.markdown("""
    * **Bulk & Block:** [NSE Large Deals](https://www.nseindia.com/market-data/large-deals)
    * **Delivery (.DAT):** [NSE All Reports](https://www.nseindia.com/all-reports)
    """)
    st.caption("Tip: On 'All Reports', search for 'Security-wise Delivery' to find the .DAT file.")
    
    st.divider()
    
    st.header("📂 Data Upload")
    uploaded_bulk = st.file_uploader("1. Bulk Deals (CSV)", type=['dat', 'csv', 'txt'])
    uploaded_block = st.file_uploader("2. Block Deals (CSV)", type=['dat', 'csv', 'txt'])
    uploaded_dat = st.file_uploader("3. Delivery File (.DAT)", type=['dat', 'csv', 'txt'])
    
    # run_button = st.button("🚀 Scan for Whales", use_container_width=True, type="primary")
    run_button = st.button("🚀 Scan for Whales", width='stretch', type="primary")

# --- MAIN EXECUTION ---
if run_button:
    if not uploaded_bulk and not uploaded_block:
        st.warning("⚠️ Please upload at least one Bulk or Block CSV file to start.")
    else:
        with st.spinner("Parsing files and generating mappings..."):
            DELIVERY_LOOKUP = load_delivery_data(uploaded_dat)
            df = get_daily_deals(uploaded_bulk, uploaded_block)
            
        if not DELIVERY_LOOKUP:
            st.toast("⚠️ No delivery data loaded. All Del % will be 0.", icon="⚠️")
        else:
            st.toast(f"✅ Delivery mapped for {len(DELIVERY_LOOKUP)} stocks!", icon="✅")

        if df.empty:
            st.error("📉 No valid deal data extracted from the CSVs.")
        else:
            df['Net_Qty'] = df.apply(lambda x: x['Quantity'] if str(x['Buy / Sell']).strip().upper() == 'BUY' else -x['Quantity'], axis=1)
            grouped = df.groupby('Symbol').agg({'Net_Qty': 'sum', 'TradePrice': 'mean'}).reset_index()
            candidates = grouped[grouped['Net_Qty'] > 0].copy()
            
            dashboard_data = []
            
            # Using a Streamlit progress bar for visual feedback
            progress_bar = st.progress(0)
            status_text = st.empty()
            total_stocks = len(candidates)
            
            for index, row in candidates.iterrows():
                symbol = row['Symbol']
                status_text.text(f"🔍 Analyzing: {symbol} ({index + 1}/{total_stocks})...")

                # 🚨 DIAGNOSTIC CHECK 1 🚨
                st.info(f"🕵️ Found {len(candidates)} stocks with Net Buying.")
                st.write("Preview of candidates:", candidates.head(3))
                
                stats = get_advanced_metrics(symbol)
                
                if stats:
                    net_buy_str = f"+{int(row['Net_Qty']):,}"
                    avg_entry = round(row['TradePrice'], 2)
                    cmp = stats['CMP']
                    diff_pct = round(((cmp - avg_entry) / avg_entry) * 100, 1) if avg_entry else 0
                    deal_val_cr = round((row['Net_Qty'] * avg_entry) / 10000000, 2)
                    equity_pct = round((row['Net_Qty'] / stats['Shares_Outstanding']) * 100, 2) if stats['Shares_Outstanding'] > 0 else 0
                    shariah = check_shariah(stats)
                    delivery = stats['Delivery_Pct']

                    passed_rules = "PASS ✅" if (equity_pct >= MIN_EQUITY_PERCENT and 
                                            delivery >= MIN_DELIVERY_PERCENT and 
                                            (not STRICT_PRICE_SUPPORT or diff_pct <= 0)) else " "

                    dashboard_data.append({
                        "Pass": passed_rules,
                        "Stock": symbol,
                        "Net Whale Buy": net_buy_str,
                        "Avg Entry": avg_entry,
                        "CMP": cmp,
                        "Diff %": f"{diff_pct}%",
                        "Deal Value (Cr)": deal_val_cr,
                        "Eq %": equity_pct,
                        "Del %": delivery,
                        "Halal": "Yes" if "YES" in shariah else "No"
                    })
                
                # progress_bar.progress((index + 1) / total_stocks)
                # This ensures the value never exceeds 1.0 (100%)
                progress_bar.progress(min((index + 1) / total_stocks, 1.0))
            
            status_text.empty() # Clear the status text when done
            
            # --- DISPLAY RESULTS ---
            final_df = pd.DataFrame(dashboard_data)
            
            if not final_df.empty:
                # Filter Box: Useful for checking specific tracked stocks
                search_term = st.text_input("🔍 Quick Search", placeholder="e.g., Integrated Industries, HDFC...")
                if search_term:
                    display_df = final_df[final_df["Stock"].str.contains(search_term, case=False, na=False)]
                else:
                    display_df = final_df.sort_values(by=["Pass", "Deal Value (Cr)"], ascending=[False, False])
                
                st.subheader(f"📊 Market Summary ({len(display_df)} Stocks Analyzed)")
                
                # Render interactive table
                # st.dataframe(
                #     display_df[["Pass", "Stock", "Net Whale Buy", "Avg Entry", "CMP", "Diff %", "Deal Value (Cr)", "Eq %", "Del %", "Halal"]], 
                #     use_container_width=True,
                #     hide_index=True
                # )

                st.dataframe(
                    display_df[["Pass", "Stock", "Net Whale Buy", "Avg Entry", "CMP", "Diff %", "Deal Value (Cr)", "Eq %", "Del %", "Halal"]], 
                    width='stretch',  # Changed from use_container_width=True
                    hide_index=True
                )
                
                # CSV Download Button
                csv = final_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download Daily Report",
                    data=csv,
                    file_name='Whale_Dashboard.csv',
                    mime='text/csv',
                )
            else:
                st.warning("No metrics could be fetched for the detected symbols.")
