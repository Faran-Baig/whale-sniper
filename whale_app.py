import time
import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import re
import cloudscraper

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
        # 1. Symbol Cleaning
        clean_symbol = str(symbol).strip().split('-')[0].split(' ')[0]
        full_symbol = f"{clean_symbol}.NS"

        # 2. Let yfinance handle the session internally
        ticker = yf.Ticker(full_symbol)

        # 3. Fetch Price (CMP) using history
        hist = ticker.history(period="1d")
        if hist.empty:
            return None
        
        cmp = float(hist['Close'].iloc[-1])

        # 4. Fetch Shares Outstanding (Triple-Check)
        # Try Fast Info first (Modern way)
        shares = ticker.fast_info.get('shares', 0)

        # Fallback to .info (Classic way)
        if shares <= 1:
            try:
                info = ticker.info
                shares = info.get('sharesOutstanding', info.get('floatShares', 0))
            except:
                shares = 0

        # Final Fallback: Market Cap / Price
        if shares <= 1:
            mcap = ticker.fast_info.get('marketCap', 0)
            if mcap > 0:
                shares = mcap / cmp

        # If we still can't find shares, we skip to avoid wrong Eq % math
        if shares <= 1:
            return None

        return {
            "Shares_Outstanding": shares,
            "CMP": round(cmp, 2),
            "Delivery_Pct": DELIVERY_LOOKUP.get(symbol, 0),
            "Market_Cap": shares * cmp,
            "Debt": 0, 
            "Sector": "N/A",
            "Industry": "N/A"
        }

    except Exception:
        # If any major error happens, just skip this stock
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

            total_candidates = len(candidates)
            
            dashboard_data = []
            
            # Using a Streamlit progress bar for visual feedback
            progress_bar = st.progress(0)
            status_text = st.empty()
            total_stocks = len(candidates)

            # 🚨 DIAGNOSTIC CHECK 1 🚨
            st.info(f"🕵️ Found {len(candidates)} stocks with Net Buying.")
            st.write("Preview of candidates:", candidates.head(3))
            
            # for index, row in candidates.iterrows():
            for index, row in candidates.reset_index(drop=True).iterrows():
                symbol = row['Symbol']
                # status_text.text(f"🔍 Analyzing: {symbol} ({index + 1}/{total_stocks})...")
                status_text.text(f"🔍 Analyzing: {row['Symbol']} ({index + 1}/{total_candidates})...")
                
                # ⏱️ STEP 1: Add a small delay to avoid "Too Many Requests" block
                time.sleep(1.5) 
                
                stats = get_advanced_metrics(symbol)
                
                # 🛡️ STEP 2: Only proceed if stats were actually fetched
                if stats:
                    net_buy_str = f"+{int(row['Net_Qty']):,}"
                    avg_entry = round(row['TradePrice'], 2)
                    cmp = stats['CMP']
                    diff_pct = round(((cmp - avg_entry) / avg_entry) * 100, 1) if avg_entry else 0
                    deal_val_cr = round((row['Net_Qty'] * avg_entry) / 10000000, 2)
                    
                    # Calculate Equity % safely
                    shares_out = stats.get('Shares_Outstanding', 0)
                    # st.write(f"DEBUG: {symbol} | Net Qty: {row['Net_Qty']} | Shares Out: {shares_out}")
                    equity_pct = round((row['Net_Qty'] / shares_out) * 100, 2) if shares_out > 0 else 0
                    
                    shariah = check_shariah(stats)
                    
                    # Ensure delivery is a float for the comparison below
                    try:
                        delivery = float(stats.get('Delivery_Pct', 0))
                    except:
                        delivery = 0.0
            
                    # ✅ Rule Check: Now delivery >= MIN_DELIVERY_PERCENT won't crash
                    passed_rules = "PASS ✅" if (
                        equity_pct >= MIN_EQUITY_PERCENT and 
                        delivery >= MIN_DELIVERY_PERCENT and 
                        (not STRICT_PRICE_SUPPORT or diff_pct <= 0)
                    ) else " "
            
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
                else:
                    # ⚠️ Optional: Log which stocks failed so you know why they are missing from the table
                    st.sidebar.warning(f"Could not fetch {symbol}")
            
                # Update progress bar safely
                # progress_bar.progress(min((index + 1) / total_stocks, 1.0))
                progress_bar.progress(min((index + 1) / total_candidates, 1.0))
            
            status_text.empty()
            
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
