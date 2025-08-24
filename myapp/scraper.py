import os
from typing import List, Dict, Optional

from django.conf import settings

try:
    import pandas as pd
except Exception:  # pandas is required for this module
    pd = None


# ---------------------- CSV utilities ----------------------
def _get_media_root() -> str:
    media_root = getattr(settings, 'MEDIA_ROOT', '.')
    os.makedirs(media_root, exist_ok=True)
    return media_root


def _resolve_csv_path(preferred_filename: Optional[str] = None) -> str:
    """Pick the CSV to read from MEDIA_ROOT.

    Priority:
    1) preferred_filename if provided and exists
    2) scrapping_prices.csv (common name in this project)
    3) scrap_prices.csv (alternate name)
    """
    media_root = _get_media_root()
    if preferred_filename:
        # If absolute path provided, respect it directly
        if os.path.isabs(preferred_filename) and os.path.exists(preferred_filename):
            return preferred_filename
        candidate = os.path.join(media_root, preferred_filename)
        if os.path.exists(candidate):
            return candidate
    for name in ("scrapping_prices.csv", "scrap_prices.csv"):
        candidate = os.path.join(media_root, name)
        if os.path.exists(candidate):
            return candidate
    # Default to scrapping_prices.csv path even if not present; caller can handle
    return os.path.join(media_root, "scrapping_prices.csv")


def _standardize_df(df: "pd.DataFrame") -> "pd.DataFrame":
    """Ensure dataframe has columns: Item, Website, Price, Link.

    Supports two schemas:
    - Long: [Item, Website, Price, Link]
    - Wide: [Item, TheKabadiwala, RecyclePay, ScrapBuddy, RecycleBaba, KabadiwalaOnline, ScrapUncle]
    """
    cols = [c.strip() for c in df.columns]
    df.columns = cols

    if {"Item", "Website", "Price"}.issubset(set(cols)):
        # Long format; ensure Price numeric and Link present
        if "Link" not in df.columns:
            df["Link"] = ""
        df["Price"] = pd.to_numeric(df["Price"], errors="coerce")
        df = df.dropna(subset=["Price"]).copy()
        return df[["Item", "Website", "Price", "Link"]]

    # Wide format: melt site columns
    site_cols = [
        "TheKabadiwala",
        "RecyclePay",
        "ScrapBuddy",
        "RecycleBaba",
        "KabadiwalaOnline",
        "ScrapUncle",
    ]
    present_site_cols = [c for c in site_cols if c in df.columns]
    if "Item" in df.columns and present_site_cols:
        melted = df.melt(id_vars=["Item"], value_vars=present_site_cols,
                         var_name="Website", value_name="Price")
        melted["Price"] = pd.to_numeric(melted["Price"], errors="coerce")
        melted = melted.dropna(subset=["Price"]).copy()
        melted["Link"] = ""
        return melted[["Item", "Website", "Price", "Link"]]

    # Unknown schema -> empty standardized frame
    return pd.DataFrame(columns=["Item", "Website", "Price", "Link"])


def load_prices_df(filename: Optional[str] = None) -> "pd.DataFrame":
    """Load the prices CSV from MEDIA_ROOT into a standardized DataFrame.

    Raises RuntimeError if pandas is unavailable or file cannot be read.
    """
    if pd is None:
        raise RuntimeError("pandas not available")

    csv_path = _resolve_csv_path(filename)
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV not found at {csv_path}")

    df = pd.read_csv(csv_path)
    return _standardize_df(df)


def query_scrap_prices(item_query: str, filename: Optional[str] = None) -> List[Dict]:
    """Filter by user input and return rows sorted by Price descending.

    - Reads CSV from MEDIA_ROOT into DataFrame
    - Normalizes schema to [Item, Website, Price, Link]
    - Filters items whose name contains the query (case-insensitive)
    - Sorts by Price descending
    - Returns list of dicts (for easy JSON/templating)
    """
    df = load_prices_df(filename)
    q = (item_query or "").strip().lower()
    if q:
        df = df[df["Item"].astype(str).str.lower().str.contains(q)]
    df = df.sort_values(by="Price", ascending=False)
    return df.to_dict(orient="records")

# import os
# import re
# import time
# from typing import Dict, List, Tuple

# try:
#     import pandas as pd  # type: ignore
# except Exception:  # pandas optional at runtime
#     pd = None  # type: ignore
# import requests
# from bs4 import BeautifulSoup
# from django.conf import settings

# SCRAP_URL = "https://scrapuncle.com/local-rate"
# CACHE_FILENAME = "scrapuncle_rates.csv"
# CACHE_TTL_SECONDS = 60 * 60  # 1 hour

# # Default fallback rates (Rs per kg) used when no CSV is present.
# # You can edit these values as per your need.
# DEFAULT_RATES: Dict[str, float] = {
#     "iron": 50.0,
#     "steel": 45.0,
#     "aluminium": 120.0,
#     "copper": 600.0,
#     "brass": 350.0,
#     "plastic": 20.0,
#     "paper": 12.0,
#     "cardboard": 10.0,
# }


# def _parse_rate(text: str) -> float:
#     """Extract numeric rate per kg from text like '₹ 100/kg' or '100 Rs/kg'."""
#     # Keep digits and dot
#     cleaned = re.findall(r"[0-9]+(?:\.[0-9]+)?", text)
#     if not cleaned:
#         return 0.0
#     try:
#         return float(cleaned[0])
#     except Exception:
#         return 0.0


# def query_scrap_prices(item_query: str, filename: str | None = None) -> list:
#     """Read prices CSV from MEDIA_ROOT, filter by item_query, and sort by Price desc.

#     Supports both schemas:
#     - Long: columns [Item, Website, Price, Link]
#     - Wide: columns [Item, TheKabadiwala, RecyclePay, ScrapBuddy, RecycleBaba, KabadiwalaOnline, ScrapUncle]

#     Returns: list of dicts with keys: Item, Website, Price, Link (sorted by Price desc)
#     """
#     # Resolve path
#     if filename:
#         csv_path = os.path.join(getattr(settings, 'MEDIA_ROOT', '.'), filename)
#     else:
#         csv_path = _media_csv_path()

#     site_urls = {
#         'TheKabadiwala': 'https://www.thekabadiwala.com/scrap-rates/Ahmadabad',
#         'RecyclePay': 'https://recyclepay.ceibagreen.com/price-list/',
#         'ScrapBuddy': 'http://scrapbuddy.in/ratecard',
#         'RecycleBaba': 'https://recyclebaba.com/scrap-price-list/',
#         'KabadiwalaOnline': 'https://www.kabadiwalaonline.com/scrap-rates/',
#         'ScrapUncle': 'https://scrapuncle.com/local-rate',
#     }
#     SITE_COLS = list(site_urls.keys())

#     q = (item_query or '').strip().lower()
#     out_rows: list[dict] = []

#     try:
#         if pd is not None:
#             df = pd.read_csv(csv_path)
#             # Wide -> Long
#             if 'Website' not in df.columns and any(col in df.columns for col in SITE_COLS):
#                 melted = []
#                 for _, rec in df.iterrows():
#                     item_name = str(rec.get('Item', '')).strip()
#                     for site in SITE_COLS:
#                         if site in df.columns:
#                             val = rec.get(site)
#                             try:
#                                 import pandas as _pd
#                                 if val is None or _pd.isna(val):
#                                     continue
#                             except Exception:
#                                 if val is None:
#                                     continue
#                             try:
#                                 price_f = float(str(val).replace(',', '').strip())
#                             except Exception:
#                                 continue
#                             if price_f <= 0:
#                                 continue
#                             melted.append({
#                                 'Item': item_name,
#                                 'Website': site,
#                                 'Price': price_f,
#                                 'Link': site_urls.get(site, '')
#                             })
#                 df = pd.DataFrame(melted, columns=['Item', 'Website', 'Price', 'Link'])
#             # Filter and sort
#             if q and 'Item' in df.columns:
#                 mask = df['Item'].astype(str).str.lower().str.contains(q)
#                 df = df[mask]
#             if 'Price' in df.columns:
#                 try:
#                     df['Price'] = pd.to_numeric(df['Price'], errors='coerce')
#                 except Exception:
#                     pass
#                 df = df.dropna(subset=['Price'])
#                 df = df.sort_values(by='Price', ascending=False)
#             out_rows = df.to_dict(orient='records')
#         else:
#             raise RuntimeError('pandas not available')
#     except Exception:
#         # csv fallback
#         try:
#             import csv as _csv
#             with open(csv_path, 'r', encoding='utf-8') as f:
#                 r = _csv.DictReader(f)
#                 fns = r.fieldnames or []
#                 using_wide = ('Website' not in fns and any(c in fns for c in SITE_COLS))
#                 if using_wide:
#                     tmp = []
#                     for rec in r:
#                         item_name = (rec.get('Item') or '').strip()
#                         if q and q not in item_name.lower():
#                             continue
#                         for site in SITE_COLS:
#                             if site in fns:
#                                 val = rec.get(site)
#                                 if val is None or str(val).strip() == '':
#                                     continue
#                                 try:
#                                     price_f = float(str(val).replace(',', '').strip())
#                                 except Exception:
#                                     continue
#                                 if price_f <= 0:
#                                     continue
#                                 tmp.append({'Item': item_name, 'Website': site, 'Price': price_f, 'Link': site_urls.get(site, '')})
#                     out_rows = sorted(tmp, key=lambda x: x['Price'], reverse=True)
#                 else:
#                     tmp = []
#                     for rec in r:
#                         item_name = (rec.get('Item') or '').strip()
#                         if q and q not in item_name.lower():
#                             continue
#                         try:
#                             price_f = float(str(rec.get('Price', '')).replace(',', '').strip())
#                         except Exception:
#                             continue
#                         tmp.append({'Item': item_name, 'Website': rec.get('Website',''), 'Price': price_f, 'Link': rec.get('Link','')})
#                     out_rows = sorted(tmp, key=lambda x: x['Price'], reverse=True)
#         except Exception:
#             out_rows = []

#     return out_rows


# def fetch_scrap_rates(force_refresh: bool = False):
#     """
#     Fetch scrap rates from SCRAP_URL, with a CSV cache in MEDIA_ROOT.

#     Returns a (DataFrame, mapping) tuple where mapping is {item_name: rate_float}.
#     """
#     # No CSV usage: directly return defaults unless force_refresh=True

#     # If not forcing refresh, return defaults without network
#     if not force_refresh:
#         # Fallback to defaults without network
#         df = None if pd is None else pd.DataFrame({
#             "Item": list(DEFAULT_RATES.keys()),
#             "Rate": [str(v) for v in DEFAULT_RATES.values()],
#         })
#         rates_map = dict(DEFAULT_RATES)
#         # Alias 'metal' to iron
#         if "iron" in rates_map:
#             rates_map["metal"] = rates_map["iron"]
#         return df, rates_map

#     # force_refresh=True path: do a live fetch
#     resp = requests.get(SCRAP_URL, timeout=20)
#     resp.raise_for_status()
#     soup = BeautifulSoup(resp.text, "html.parser")

#     names = soup.find_all("p", class_="rate_name")
#     prices = soup.find_all("p", class_="rate_price")

#     items: List[str] = []
#     rates: List[str] = []

#     for name, price in zip(names, prices):
#         items.append(name.get_text(strip=True))
#         rates.append(price.get_text(strip=True))

#     df = None
#     if pd is not None:
#         df = pd.DataFrame({"Item": items, "Rate": rates})

#     rates_map = {item: _parse_rate(rate) for item, rate in zip(items, rates)}
#     # Alias: map 'metal' to iron's rate if present
#     lower_keys = {k.lower(): k for k in rates_map}
#     if 'iron' in lower_keys:
#         rates_map['metal'] = rates_map[lower_keys['iron']]
#     return df, rates_map

# ---------------------- CSV cache build + query ----------------------
CACHE_FILENAME = "scrap_prices.csv"


def _media_csv_path() -> str:
    media_root = getattr(settings, 'MEDIA_ROOT', '.')
    os.makedirs(media_root, exist_ok=True)
    return os.path.join(media_root, CACHE_FILENAME)


def _write_rows_csv(path: str, rows: list):
    headers = ["Item", "Website", "Price", "Link"]
    try:
        if pd is not None:
            df = pd.DataFrame(rows, columns=headers)
            df.to_csv(path, index=False)
        else:
            with open(path, 'w', encoding='utf-8', newline='') as f:
                w = csv.writer(f)
                w.writerow(headers)
                for r in rows:
                    w.writerow(r)
    except Exception:
        pass


def build_scrap_prices_csv(force_refresh: bool = False) -> str:
    """Scrape ALL items and cache under MEDIA_ROOT/scrap_prices.csv.
    If file exists and not force_refresh, reuse it.
    """
    csv_path = _media_csv_path()
    try:
        if (not force_refresh) and os.path.exists(csv_path):
            return csv_path
    except Exception:
        pass

    rows = []
    for fn in [
        scrape_scrapuncle_all,
        scrape_thekabadiwala_all,
        scrape_recyclepay_all,
        scrape_scrapbuddy_all,
        scrape_recyclebaba_all,
        scrape_kabadiwalaonline_all,
    ]:
        try:
            rows.extend(fn())
        except Exception:
            continue

    # Deduplicate by (item, website)
    dedup = {}
    for item_name, site, price, link in rows:
        key = (item_name, site)
        if key not in dedup or (isinstance(price, (int, float)) and price):
            dedup[key] = [item_name, site, price, link]
    final_rows = list(dedup.values())

    _write_rows_csv(csv_path, final_rows)
    return csv_path


def build_recyclebaba_scrapbuddy_csv(filename: str = "scrap_prices_recyclebaba_scrapbuddy.csv", force_refresh: bool = True) -> str:
    media_root = getattr(settings, 'MEDIA_ROOT', '.')
    os.makedirs(media_root, exist_ok=True)
    csv_path = os.path.join(media_root, filename)

    try:
        if (not force_refresh) and os.path.exists(csv_path):
            return csv_path
    except Exception:
        pass

    rows = []
    try:
        rows.extend(scrape_recyclebaba_all())
    except Exception:
        pass
    try:
        rows.extend(scrape_scrapbuddy_all())
    except Exception:
        pass

    dedup = {}
    for item_name, site, price, link in rows:
        key = (item_name, site)
        if key not in dedup or (isinstance(price, (int, float)) and price):
            dedup[key] = [item_name, site, price, link]
    final_rows = list(dedup.values())

    _write_rows_csv(csv_path, final_rows)
    return csv_path


def query_scrap_prices(item_query: str, filename: str | None = None) -> list:
    """Read CSV from MEDIA_ROOT, filter by item_query, sort by Price desc.
    Supports both long and wide schemas. Returns list of dicts.
    """
    media_root = getattr(settings, 'MEDIA_ROOT', '.')
    if filename:
        # If absolute, use as-is; else, resolve under MEDIA_ROOT
        csv_path = filename if os.path.isabs(filename) else os.path.join(media_root, filename)
    else:
        # Prefer an existing 'scrapping_prices.csv' (as per your media folder), else default cache
        preferred = os.path.join(media_root, 'scrapping_prices.csv')
        csv_path = preferred if os.path.exists(preferred) else _media_csv_path()

    site_urls = {
        'TheKabadiwala': 'https://www.thekabadiwala.com/scrap-rates/Ahmadabad',
        'RecyclePay': 'https://recyclepay.ceibagreen.com/price-list/',
        'ScrapBuddy': 'http://scrapbuddy.in/ratecard',
        'RecycleBaba': 'https://recyclebaba.com/scrap-price-list/',
        'KabadiwalaOnline': 'https://www.kabadiwalaonline.com/scrap-rates/',
        'ScrapUncle': 'https://scrapuncle.com/local-rate',
    }
    SITE_COLS = list(site_urls.keys())

    q = (item_query or '').strip().lower()
    out_rows: list[dict] = []

    try:
        if pd is not None:
            df = pd.read_csv(csv_path)
            if 'Website' not in df.columns and any(col in df.columns for col in SITE_COLS):
                melted = []
                for _, rec in df.iterrows():
                    item_name = str(rec.get('Item', '')).strip()
                    for site in SITE_COLS:
                        if site in df.columns:
                            val = rec.get(site)
                            try:
                                import pandas as _pd
                                if val is None or _pd.isna(val):
                                    continue
                            except Exception:
                                if val is None:
                                    continue
                            try:
                                price_f = float(str(val).replace(',', '').strip())
                            except Exception:
                                continue
                            if price_f <= 0:
                                continue
                            melted.append({'Item': item_name, 'Website': site, 'Price': price_f, 'Link': site_urls.get(site, '')})
                df = pd.DataFrame(melted, columns=['Item', 'Website', 'Price', 'Link'])
            if q and 'Item' in df.columns:
                df = df[df['Item'].astype(str).str.lower().str.contains(q)]
            if 'Price' in df.columns:
                try:
                    df['Price'] = pd.to_numeric(df['Price'], errors='coerce')
                except Exception:
                    pass
                df = df.dropna(subset=['Price']).sort_values(by='Price', ascending=False)
            out_rows = df.to_dict(orient='records')
        else:
            raise RuntimeError('pandas not available')
    except Exception:
        try:
            import csv as _csv
            with open(csv_path, 'r', encoding='utf-8') as f:
                r = _csv.DictReader(f)
                fns = r.fieldnames or []
                using_wide = ('Website' not in fns and any(c in fns for c in SITE_COLS))
                if using_wide:
                    tmp = []
                    for rec in r:
                        item_name = (rec.get('Item') or '').strip()
                        if q and q not in item_name.lower():
                            continue
                        for site in SITE_COLS:
                            if site in fns:
                                val = rec.get(site)
                                if val is None or str(val).strip() == '':
                                    continue
                                try:
                                    price_f = float(str(val).replace(',', '').strip())
                                except Exception:
                                    continue
                                if price_f <= 0:
                                    continue
                                tmp.append({'Item': item_name, 'Website': site, 'Price': price_f, 'Link': site_urls.get(site, '')})
                    out_rows = sorted(tmp, key=lambda x: x['Price'], reverse=True)
                else:
                    tmp = []
                    for rec in r:
                        item_name = (rec.get('Item') or '').strip()
                        if q and q not in item_name.lower():
                            continue
                        try:
                            price_f = float(str(rec.get('Price', '')).replace(',', '').strip())
                        except Exception:
                            continue
                        tmp.append({'Item': item_name, 'Website': rec.get('Website',''), 'Price': price_f, 'Link': rec.get('Link','')})
                    out_rows = sorted(tmp, key=lambda x: x['Price'], reverse=True)
        except Exception:
            out_rows = []

    return out_rows
