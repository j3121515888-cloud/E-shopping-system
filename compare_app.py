# -*- coding: utf-8 -*-
"""PChome vs MOMO 商品價格比較分析 (Streamlit)

執行方式：streamlit run compare_app.py
"""
import re
import time
from datetime import datetime
from urllib.parse import quote

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from curl_cffi import requests as cffi_requests

st.set_page_config(page_title="PChome vs MOMO 價格比較", page_icon="🛒", layout="wide")

# ---------------------------------------------------------------
# 常數
# ---------------------------------------------------------------
PCHOME_API = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
PCHOME_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

MOMO_API = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
MOMO_HOME = "https://www.momoshop.com.tw/"
MOMO_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}

BINS = [0, 500, 1000, 1500, 2000, 3000, 5000, float("inf")]
LABELS = ["<500", "500-999", "1000-1499", "1500-1999", "2000-2999", "3000-4999", "5000+"]
COLORS = {"PChome": "#1f77b4", "MOMO": "#e8590c"}


# ---------------------------------------------------------------
# 爬蟲：PChome
# ---------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=600)
def fetch_pchome_page(keyword: str, page: int) -> list:
    url = f"{PCHOME_API}?q={quote(keyword)}&page={page}&sort=sale/dc"
    resp = requests.get(url, headers=PCHOME_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json().get("prods") or []


def crawl_pchome(keyword: str, pages: int, delay: float, bar, offset: float, span: float):
    """回傳 (DataFrame, 訊息list)。"""
    rows, msgs = [], []
    for i in range(1, pages + 1):
        bar.progress(min(offset + span * (i - 1) / pages, 1.0), text=f"PChome 第 {i}/{pages} 頁…")
        try:
            prods = fetch_pchome_page(keyword, i)
        except (requests.RequestException, ValueError) as e:
            msgs.append(("warning", f"PChome 第 {i} 頁抓取失敗：{e}"))
            break
        if not prods:
            msgs.append(("info", f"PChome 第 {i} 頁沒有資料，停止爬取。"))
            break
        for p in prods:
            rows.append({"頁數": i, "品名": p.get("name", ""), "價格": p.get("price")})
        if i < pages:
            time.sleep(delay)
    return pd.DataFrame(rows), msgs


# ---------------------------------------------------------------
# 爬蟲：MOMO
# ---------------------------------------------------------------
def momo_payload(keyword: str, page: int) -> dict:
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "searchValue": keyword,
            "searchType": "1",
            "cateLevel": "-1",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "showType": "chessboardType",
            "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "",
                "cateLv": "-1",
                "keyword": keyword,
                "curPage": str(page),
                "historyDoPush": "false",
                "timestamp": int(time.time() * 1000),
            },
            "flag": 2018,
            "serviceCode": "MT01",
            "addressSearchData": {},
        },
    }


def crawl_momo(keyword: str, pages: int, delay: float, bar, offset: float, span: float):
    rows, msgs = [], []
    try:
        session = cffi_requests.Session(impersonate="chrome")  # 模擬 Chrome TLS 指紋
        session.get(MOMO_HOME, headers=MOMO_HEADERS)  # 先取得 cookie
    except Exception as e:
        return pd.DataFrame(), [("warning", f"MOMO 連線失敗：{e}")]

    for i in range(1, pages + 1):
        bar.progress(min(offset + span * (i - 1) / pages, 1.0), text=f"MOMO 第 {i}/{pages} 頁…")
        try:
            resp = session.post(MOMO_API, headers=MOMO_HEADERS, json=momo_payload(keyword, i))
        except Exception as e:
            msgs.append(("warning", f"MOMO 第 {i} 頁請求失敗：{e}"))
            break
        if resp.status_code != 200:
            msgs.append(("warning", f"MOMO 第 {i} 頁回應狀態 {resp.status_code}：{resp.text[:150]}"))
            break
        try:
            goods = resp.json().get("rtnSearchData", {}).get("goodsInfoList", [])
        except ValueError as e:
            msgs.append(("warning", f"MOMO 第 {i} 頁解析失敗：{e}"))
            break
        if not goods:
            msgs.append(("info", f"MOMO 第 {i} 頁沒有資料，停止爬取。"))
            break
        for g in goods:
            rows.append({"頁數": i, "品名": g.get("goodsName", ""), "價格": g.get("goodsPrice", "")})
        if i < pages:
            time.sleep(delay)
    return pd.DataFrame(rows), msgs


# ---------------------------------------------------------------
# 資料處理
# ---------------------------------------------------------------
def extract_brand(name: str) -> str:
    """優先取【】或 [] 內文字當品牌，否則取品名第一個詞。"""
    name = str(name).strip()
    m = re.search(r"[【\[](.+?)[】\]]", name)
    if m:
        return m.group(1).strip()
    first = re.split(r"[\s/／,，]", name)[0] if name else ""
    return first[:10] or "未知"


def clean(df: pd.DataFrame, platform: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["價格"] = out["價格"].astype(str).str.replace(r"[^\d]", "", regex=True)
    out = out[out["價格"] != ""].copy()
    out["價格"] = out["價格"].astype(int)
    out = out.drop_duplicates(subset=["品名", "價格"]).reset_index(drop=True)
    out.insert(0, "平台", platform)
    out["品牌"] = out["品名"].map(extract_brand)
    out["價格區間"] = pd.cut(out["價格"], bins=BINS, labels=LABELS, right=False).astype(str)
    return out


def run_crawl(keyword: str, pages: int, delay: float, platforms: list):
    bar = st.progress(0.0, text="開始爬取…")
    frames, msgs = [], []
    span = 1.0 / len(platforms)
    for idx, p in enumerate(platforms):
        fn = crawl_pchome if p == "PChome" else crawl_momo
        df, m = fn(keyword, pages, delay, bar, idx * span, span)
        msgs += m
        df = clean(df, p)
        if df.empty:
            msgs.append(("error", f"{p} 沒有取得任何資料。"))
        else:
            frames.append(df)
    bar.progress(1.0, text="爬取完成")
    bar.empty()
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return data, msgs


# ---------------------------------------------------------------
# 圖表
# ---------------------------------------------------------------
def line_chart(df: pd.DataFrame, kw: str, top_n: int):
    parts = []
    for p, g in df.groupby("平台", sort=False):
        g = g.head(top_n).copy()
        g["序號"] = range(1, len(g) + 1)
        parts.append(g)
    d = pd.concat(parts)
    fig = px.line(
        d, x="序號", y="價格", color="平台", markers=True,
        color_discrete_map=COLORS, hover_data={"品名": True, "價格": ":,.0f"},
        title=f"「{kw}」售價折線圖（各平台前 {top_n} 筆）",
    )
    for p, g in d.groupby("平台"):
        fig.add_hline(
            y=g["價格"].mean(), line_dash="dash", line_color=COLORS.get(p),
            annotation_text=f"{p} 平均 {g['價格'].mean():,.0f}",
            annotation_position="top left" if p == "PChome" else "bottom right",
        )
    fig.update_layout(height=550, xaxis_title="商品順序", yaxis_title="價格 (NTD)")
    return fig


def pie_chart(df: pd.DataFrame, platform: str, kw: str):
    g = df[df["平台"] == platform]
    counts = g["價格區間"].value_counts().reindex(LABELS).dropna().reset_index()
    counts.columns = ["價格區間", "商品數"]
    fig = px.pie(counts, names="價格區間", values="商品數", hole=0.3,
                 category_orders={"價格區間": LABELS},
                 title=f"{platform}「{kw}」價格區間分布")
    fig.update_traces(textinfo="percent+label+value", sort=False)
    fig.update_layout(height=500)
    return fig


def sunburst_chart(df: pd.DataFrame, top_n: int, kw: str):
    d = df.copy()
    # 每個「平台＋價格區間」內，只保留商品數前 N 的品牌，其餘歸為「其他」
    cnt = d.groupby(["平台", "價格區間", "品牌"]).size().rename("n").reset_index()
    cnt["rank"] = cnt.groupby(["平台", "價格區間"])["n"].rank(method="first", ascending=False)
    keep = set(map(tuple, cnt.loc[cnt["rank"] <= top_n, ["平台", "價格區間", "品牌"]].values))
    d["品牌"] = [b if (p, r, b) in keep else "其他"
                 for p, r, b in zip(d["平台"], d["價格區間"], d["品牌"])]
    g = d.groupby(["平台", "價格區間", "品牌"]).size().reset_index(name="商品數")
    fig = px.sunburst(g, path=["平台", "價格區間", "品牌"], values="商品數",
                      color="平台", color_discrete_map=COLORS,
                      title=f"「{kw}」平台 → 價格區間 → 品牌")
    fig.update_traces(textinfo="label+value")
    fig.update_layout(height=700)
    return fig


def bar_range_chart(df: pd.DataFrame, kw: str, as_percent: bool):
    g = df.groupby(["平台", "價格區間"]).size().reset_index(name="商品數")
    if as_percent:
        g["占比 (%)"] = (g["商品數"] / g.groupby("平台")["商品數"].transform("sum") * 100).round(1)
        y, ytitle = "占比 (%)", "占比 (%)"
    else:
        y, ytitle = "商品數", "商品數"
    fig = px.bar(g, x="價格區間", y=y, color="平台", barmode="group", text=y,
                 category_orders={"價格區間": LABELS}, color_discrete_map=COLORS,
                 title=f"「{kw}」各價格區間商品{'占比' if as_percent else '數量'}比較")
    fig.update_layout(height=500, yaxis_title=ytitle)
    return fig


def bar_stats_chart(df: pd.DataFrame, kw: str):
    s = df.groupby("平台")["價格"].agg(平均="mean", 中位數="median", 最高="max", 最低="min").reset_index()
    m = s.melt(id_vars="平台", var_name="統計量", value_name="價格")
    m["價格"] = m["價格"].round(0)
    fig = px.bar(m, x="統計量", y="價格", color="平台", barmode="group", text="價格",
                 color_discrete_map=COLORS, title=f"「{kw}」價格統計比較")
    fig.update_traces(texttemplate="%{text:,.0f}")
    fig.update_layout(height=500, yaxis_title="價格 (NTD)")
    return fig


def bar_brand_chart(df: pd.DataFrame, kw: str, top_n: int):
    top = df["品牌"].value_counts().head(top_n).index
    g = df[df["品牌"].isin(top)].groupby(["品牌", "平台"]).size().reset_index(name="商品數")
    fig = px.bar(g, x="品牌", y="商品數", color="平台", barmode="group",
                 category_orders={"品牌": list(top)}, color_discrete_map=COLORS,
                 title=f"「{kw}」熱門品牌商品數比較（前 {top_n} 大）")
    fig.update_layout(height=500)
    return fig


# ---------------------------------------------------------------
# UI
# ---------------------------------------------------------------
st.title("🛒 PChome vs MOMO 商品價格比較")

with st.sidebar:
    st.header("搜尋設定")
    keyword = st.text_input("產品名稱", value="行動電源")
    pages = st.number_input("爬取頁數（每個平台）", min_value=1, max_value=20, value=2, step=1)
    platforms = st.multiselect("比較平台", ["PChome", "MOMO"], default=["PChome", "MOMO"])
    delay = st.slider("每頁間隔秒數", 0.5, 10.0, 1.5, 0.5, help="間隔太短可能被網站封鎖")
    run = st.button("🔍 開始爬取", type="primary", use_container_width=True)

if run:
    if not keyword.strip():
        st.error("請輸入產品名稱")
    elif not platforms:
        st.error("請至少選擇一個平台")
    else:
        data, msgs = run_crawl(keyword.strip(), int(pages), delay, platforms)
        for level, text in msgs:
            getattr(st, level)(text)
        if data.empty:
            st.session_state.pop("data", None)
        else:
            st.session_state["data"] = data
            st.session_state["keyword"] = keyword.strip()

if "data" not in st.session_state:
    st.info("請在左側輸入產品名稱與頁數，然後按「開始爬取」。")
    st.stop()

df = st.session_state["data"]
kw = st.session_state["keyword"]
plats = [p for p in ["PChome", "MOMO"] if p in df["平台"].unique()]

# ---- 各平台統計卡片
for p in plats:
    g = df[df["平台"] == p]
    st.subheader(p)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("商品數", f"{len(g):,}")
    c2.metric("平均價格", f"${g['價格'].mean():,.0f}")
    c3.metric("中位數", f"${g['價格'].median():,.0f}")
    c4.metric("最高價格", f"${g['價格'].max():,}")
    c5.metric("最低價格", f"${g['價格'].min():,}")

if len(plats) < 2:
    st.caption("目前只有一個平台的資料，比較圖表僅顯示單一平台。")

tab_line, tab_pie, tab_sun, tab_bar, tab_data = st.tabs(
    ["📈 折線圖", "🥧 圓餅圖", "☀️ 旭日圖", "📊 柱狀圖", "📋 資料表"]
)

with tab_line:
    top_n = st.slider("每個平台顯示前 N 筆", 10, 500, 70, 10)
    st.plotly_chart(line_chart(df, kw, top_n), use_container_width=True)

with tab_pie:
    cols = st.columns(len(plats))
    for col, p in zip(cols, plats):
        col.plotly_chart(pie_chart(df, p, kw), use_container_width=True)

with tab_sun:
    sb_n = st.slider("每個價格區間最多顯示的品牌數（其餘歸為「其他」）", 3, 15, 5)
    st.plotly_chart(sunburst_chart(df, sb_n, kw), use_container_width=True)

with tab_bar:
    mode = st.radio("價格區間比較方式", ["商品數量", "占比 (%)"], horizontal=True)
    st.plotly_chart(bar_range_chart(df, kw, mode == "占比 (%)"), use_container_width=True)
    st.plotly_chart(bar_stats_chart(df, kw), use_container_width=True)
    brand_n = st.slider("熱門品牌顯示數", 3, 20, 10)
    st.plotly_chart(bar_brand_chart(df, kw, brand_n), use_container_width=True)

with tab_data:
    view = st.radio("篩選平台", ["全部"] + plats, horizontal=True)
    show = df if view == "全部" else df[df["平台"] == view]
    st.dataframe(show, use_container_width=True, height=500)
    st.download_button(
        "⬇️ 下載 CSV",
        data=show.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{datetime.now():%Y%m%d}_比價_{kw}.csv",
        mime="text/csv",
    )
