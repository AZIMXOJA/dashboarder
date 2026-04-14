import os, io, csv, json
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

app = FastAPI(title="Smartup Dashboard API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SHEET_ID = os.environ.get("SHEET_ID", "")
USD_RATE  = float(os.environ.get("USD_RATE", "12700"))
FRONTEND  = Path(__file__).parent.parent / "frontend"

def _parse_gids():
    out = {}
    for part in os.environ.get("SHEET_GIDS", "").split(","):
        if ":" in part:
            k, v = part.strip().split(":", 1)
            out[k.strip()] = v.strip()
    return out
SHEET_GIDS = _parse_gids()

DEFAULT_GIDS = {
    "Config":"0","Rooms":"1","KpiPlans":"2","KpiProductTypePlans":"3",
    "Filials":"4","PriceTypes":"5","Users":"6","Products":"7",
    "ProductGroups":"8","ProductGroupTypes":"9","Clients":"10",
    "ClientGroups":"11","ClientGroupTypes":"12","Orders":"13",
    "Returns":"15","Currencies":"16",
}

# ── helpers ──────────────────────────────────────────────
async def fetch_sheet(name):
    gid = SHEET_GIDS.get(name) or DEFAULT_GIDS.get(name, "0")
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}"
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(url); r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text)))

def sf(v, d=0.0):
    try: return float(str(v).replace(" ","").replace(",",".")) if v else d
    except: return d

def parse_dt(v):
    if not v: return None
    s = str(v)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y"):
        try: return datetime.strptime(s[:19], fmt)
        except: pass
    return None

def to_uzs(a, cur, rate): return a * rate if cur == "840" else a
def to_disp(a, cur, rate): return a / rate if cur == "USD" and rate else a
def fmtr(v): return round(v, 2)
def month_start(dt): return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
def prev_month_start(dt): return month_start(month_start(dt) - timedelta(days=1))
def delta_pct(c, p): return round((c - p) / p * 100, 1) if p else None

def get_range(period, now):
    if period == "today": return now.replace(hour=0,minute=0,second=0), now
    if period == "week":  return now - timedelta(days=7), now
    return month_start(now), now

def filter_rows(rows, filial_id, df, dt, status="A"):
    out = []
    for r in rows:
        if filial_id and r.get("filial_id") != filial_id: continue
        if status and r.get("status") != status: continue
        d = parse_dt(r.get("deal_time"))
        if df and d and d < df: continue
        if dt and d and d > dt: continue
        out.append(r)
    return out

# ── frontend ─────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    p = FRONTEND / "index.html"
    if not p.exists(): return HTMLResponse("<h1>Frontend not found</h1>", 404)
    html = p.read_text()
    api_url = os.environ.get("API_URL", "")
    if api_url:
        html = html.replace("window.DASHBOARD_API || 'http://localhost:8000'", f"'{api_url}'")
    return HTMLResponse(html)

@app.get("/health")
async def health():
    return {"status": "ok", "sheet": SHEET_ID[:8] + "..." if SHEET_ID else "NOT_SET"}

# ── /api/meta ────────────────────────────────────────────
@app.get("/api/meta")
async def api_meta():
    filials     = await fetch_sheet("Filials")
    currencies  = await fetch_sheet("Currencies")
    rate = USD_RATE
    for c in currencies:
        if c.get("code") == "840" and c.get("currency_rate"):
            rate = sf(c["currency_rate"], USD_RATE); break
    flist = [{"filial_id": r["filial_id"], "name": r["name"]}
             for r in filials if r.get("state") == "A" and r.get("name", "") != "Администрирование"]
    return {"filials": flist, "usd_rate": rate}

# ── /api/summary ─────────────────────────────────────────
@app.get("/api/summary")
async def api_summary(filial_id: str = "", period: str = "month", currency: str = "UZS"):
    orders  = await fetch_sheet("Orders")
    returns = await fetch_sheet("Returns")
    clients = await fetch_sheet("Clients")
    rate = USD_RATE; now = datetime.now()
    df, dt = get_range(period, now)
    plen = (dt - df).total_seconds()
    pdf, pdt = df - timedelta(seconds=plen), df

    def calc(fid, rdf, rdt):
        rev = disc = gift = wn = wb = 0.0
        deals = set(); persons = set()
        for r in filter_rows(orders, fid or None, rdf, rdt, "A"):
            lt  = r.get("line_type", "")
            amt = to_uzs(sf(r.get("sold_amount")), r.get("currency_code", "860"), rate)
            if lt == "product":
                rev += amt; disc += abs(sf(r.get("margin_amount")))
                deals.add(r.get("deal_id")); persons.add(r.get("person_id"))
                wn += sf(r.get("total_weight_netto")); wb += sf(r.get("total_weight_brutto"))
            elif lt == "gift":
                gift += abs(amt)
        return dict(revenue=rev, orders=len(deals), akb=len(persons), disc=disc, gift=gift, wn=wn, wb=wb)

    c = calc(filial_id, df, dt)
    p = calc(filial_id, pdf, pdt)
    okb = len({r.get("person_id") for r in clients if r.get("is_client") == "Y"
               and (not filial_id or r.get("filial_id") == filial_id)})
    seen = set(); rs = rc = 0
    for r in filter_rows(returns, filial_id or None, df, dt, None):
        did = r.get("deal_id")
        if did not in seen:
            seen.add(did); rs += abs(to_uzs(sf(r.get("total_amount")), r.get("currency_code","860"), rate)); rc += 1

    def cv(v): return fmtr(to_disp(v, currency, rate))
    return {
        "period": {"from": df.isoformat(), "to": dt.isoformat()},
        "currency": currency, "usd_rate": rate,
        "revenue": cv(c["revenue"]),      "revenue_delta": delta_pct(c["revenue"], p["revenue"]),
        "orders": c["orders"],             "orders_delta":  delta_pct(c["orders"],  p["orders"]),
        "akb": c["akb"],                   "akb_delta":     delta_pct(c["akb"],     p["akb"]),
        "okb": okb,
        "discount": cv(c["disc"]),         "discount_pct": round(c["disc"]/c["revenue"]*100, 2) if c["revenue"] else 0,
        "gift_amount": cv(c["gift"]),
        "returns_sum": cv(rs),             "returns_count": rc,
        "returns_pct": round(rs/c["revenue"]*100, 2) if c["revenue"] else 0,
        "weight_netto": round(c["wn"], 1), "weight_brutto": round(c["wb"], 1),
        "avg_order": cv(c["revenue"] / c["orders"]) if c["orders"] else 0,
    }

# ── /api/chart/revenue_by_day ────────────────────────────
@app.get("/api/chart/revenue_by_day")
async def chart_day(filial_id: str = "", period: str = "month", currency: str = "UZS"):
    orders = await fetch_sheet("Orders")
    rate = USD_RATE; now = datetime.now(); df, _ = get_range(period, now)
    by_day = defaultdict(float)
    for r in filter_rows(orders, filial_id or None, df, now, "A"):
        if r.get("line_type") != "product": continue
        d = parse_dt(r.get("deal_time"))
        if d: by_day[d.strftime("%Y-%m-%d")] += to_uzs(sf(r.get("sold_amount")), r.get("currency_code","860"), rate)
    days = []; cur = df.date(); end = now.date()
    while cur <= end:
        ds = cur.strftime("%Y-%m-%d")
        days.append({"date": ds, "label": cur.strftime("%d %b"),
                     "revenue": fmtr(to_disp(by_day.get(ds, 0), currency, rate))})
        cur += timedelta(days=1)
    return {"data": days, "currency": currency}

# ── /api/chart/revenue_by_filial ─────────────────────────
@app.get("/api/chart/revenue_by_filial")
async def chart_filial(period: str = "month", currency: str = "UZS"):
    orders = await fetch_sheet("Orders"); filials = await fetch_sheet("Filials")
    rate = USD_RATE; now = datetime.now(); df, _ = get_range(period, now)
    fnames = {r["filial_id"]: r["name"] for r in filials}
    by_f = defaultdict(float); ords = defaultdict(set)
    for r in filter_rows(orders, None, df, now, "A"):
        if r.get("line_type") != "product": continue
        fid = r.get("filial_id","")
        by_f[fid] += to_uzs(sf(r.get("sold_amount")), r.get("currency_code","860"), rate)
        ords[fid].add(r.get("deal_id"))
    data = sorted([{"filial_id": f, "name": fnames.get(f, f),
                    "revenue": fmtr(to_disp(v, currency, rate)), "orders": len(ords[f])}
                   for f, v in by_f.items()], key=lambda x: -x["revenue"])
    return {"data": data, "currency": currency}

# ── /api/agents ──────────────────────────────────────────
@app.get("/api/agents")
async def api_agents(filial_id: str = "", period: str = "month", currency: str = "UZS"):
    orders = await fetch_sheet("Orders"); kpi = await fetch_sheet("KpiPlans")
    rate = USD_RATE; now = datetime.now(); df, dt = get_range(period, now)
    ag = defaultdict(lambda: dict(rev=0.0, disc=0.0, gift=0.0, deals=set(), clients=set(), rooms=set()))
    for r in filter_rows(orders, filial_id or None, df, dt, "A"):
        lt = r.get("line_type",""); name = r.get("sales_manager_name") or r.get("sales_manager_code") or "Unknown"
        amt = to_uzs(sf(r.get("sold_amount")), r.get("currency_code","860"), rate)
        if lt == "product":
            ag[name]["rev"] += amt; ag[name]["disc"] += abs(sf(r.get("margin_amount")))
            ag[name]["deals"].add(r.get("deal_id")); ag[name]["clients"].add(r.get("person_id"))
            ag[name]["rooms"].add(r.get("room_id",""))
        elif lt == "gift": ag[name]["gift"] += abs(amt)
    kmap = {k.get("room_id",""): sf(k.get("s_value"))
            for k in kpi if not filial_id or k.get("filial_id") == filial_id}
    def cv(v): return fmtr(to_disp(v, currency, rate))
    result = []
    for name, d in ag.items():
        plan = sum(kmap.get(r, 0) for r in d["rooms"]); rev = d["rev"]; n = len(d["deals"])
        result.append({"name": name, "revenue": cv(rev), "orders": n,
                       "akb": len(d["clients"]), "avg_order": cv(rev/n) if n else 0,
                       "discount": cv(d["disc"]), "plan": cv(plan),
                       "plan_pct": round(rev/plan*100,1) if plan else None})
    return {"data": sorted(result, key=lambda x: -x["revenue"]), "currency": currency}

# ── /api/products ────────────────────────────────────────
@app.get("/api/products")
async def api_products(filial_id: str = "", period: str = "month",
                       currency: str = "UZS", group_by: str = "sku", limit: int = 20):
    orders   = await fetch_sheet("Orders")
    pg_types = await fetch_sheet("ProductGroupTypes")
    prods    = await fetch_sheet("Products")
    rate = USD_RATE; now = datetime.now(); df, dt = get_range(period, now)
    pg = {}
    for p in prods:
        try: pg[p.get("product_id","")] = json.loads(p.get("group_ids") or "[]")
        except: pg[p.get("product_id","")] = []
    gnames = {r.get("product_group_id",""): r.get("group_name","") for r in pg_types}
    tnames = {r.get("product_type_id",""):  r.get("name","")       for r in pg_types}
    data = defaultdict(lambda: dict(rev=0.0, qty=0.0, deals=set(), gifts=0))
    for r in filter_rows(orders, filial_id or None, df, dt, "A"):
        lt = r.get("line_type",""); pid = r.get("product_id","")
        amt = to_uzs(sf(r.get("sold_amount")), r.get("currency_code","860"), rate)
        if lt == "product":
            if group_by == "sku": key = r.get("product_name") or r.get("product_code") or pid
            elif group_by == "inventory_kind":
                key = {"G":"Товары","P":"Продукция","M":"Сырьё"}.get(r.get("inventory_kind",""),"Прочее")
            else:
                key = "Не указано"
                for g in pg.get(pid, []):
                    gn = gnames.get(g.get("group_id",""),""); tn = tnames.get(g.get("type_id",""),"")
                    if group_by == "group"    and "Группа"    in gn: key = tn or gn; break
                    if group_by == "category" and "Категория" in gn: key = tn or gn; break
                    if group_by == "brand"    and "Торговая"  in gn: key = tn or gn; break
            data[key]["rev"] += amt; data[key]["qty"] += sf(r.get("sold_quant")); data[key]["deals"].add(r.get("deal_id"))
        elif lt == "gift" and group_by == "sku":
            key = r.get("product_name") or r.get("product_code") or pid; data[key]["gifts"] += 1
    def cv(v): return fmtr(to_disp(v, currency, rate))
    result = sorted([{"name": k, "revenue": cv(v["rev"]), "qty": round(v["qty"]),
                      "orders": len(v["deals"]), "gifts": v["gifts"]}
                     for k, v in data.items()], key=lambda x: -x["revenue"])[:limit]
    return {"data": result, "currency": currency, "group_by": group_by}

# ── /api/kpi ─────────────────────────────────────────────
@app.get("/api/kpi")
async def api_kpi(filial_id: str = "", currency: str = "UZS"):
    orders = await fetch_sheet("Orders"); kpi = await fetch_sheet("KpiPlans")
    rate = USD_RATE; now = datetime.now(); df = month_start(now)
    frev = defaultdict(float); fdeal = defaultdict(set); fakb = defaultdict(set)
    for r in filter_rows(orders, filial_id or None, df, now, "A"):
        if r.get("line_type") != "product": continue
        rid = r.get("room_id",""); amt = to_uzs(sf(r.get("sold_amount")), r.get("currency_code","860"), rate)
        frev[rid] += amt; fdeal[rid].add(r.get("deal_id")); fakb[rid].add(r.get("person_id"))
    plans = {k.get("room_id",""): {"name": k.get("room_name",""), "s": sf(k.get("s_value")),
             "q": sf(k.get("q_value")), "a": sf(k.get("a_value"))}
             for k in kpi if not filial_id or k.get("filial_id") == filial_id}
    all_rooms = set(list(plans) + list(frev))
    def cv(v): return fmtr(to_disp(v, currency, rate))
    result = []
    for rid in all_rooms:
        p = plans.get(rid, {}); rev = frev.get(rid,0); ps=p.get("s",0); pq=p.get("q",0); pa=p.get("a",0)
        fq = len(fdeal.get(rid,set())); fa = len(fakb.get(rid,set()))
        result.append({"room_id": rid, "room_name": p.get("name") or rid,
                       "plan_rev": cv(ps), "fact_rev": cv(rev),
                       "pct_rev": round(rev/ps*100,1) if ps else None,
                       "plan_q": round(pq), "fact_q": fq,
                       "pct_q": round(fq/pq*100,1) if pq else None,
                       "plan_a": round(pa), "fact_a": fa,
                       "pct_a": round(fa/pa*100,1) if pa else None})
    return {"data": sorted(result, key=lambda x: -(x["fact_rev"] or 0)), "currency": currency}

# ── /api/clients ─────────────────────────────────────────
@app.get("/api/clients")
async def api_clients(filial_id: str = "", currency: str = "UZS"):
    orders  = await fetch_sheet("Orders")
    clients = await fetch_sheet("Clients")
    rate = USD_RATE; now = datetime.now()
    cf = month_start(now); pf = prev_month_start(now)
    cur_f  = filter_rows(orders, filial_id or None, cf,  now, "A")
    prev_f = filter_rows(orders, filial_id or None, pf,  cf,  "A")
    cur_b  = {r.get("person_id") for r in cur_f  if r.get("line_type") == "product"}
    prev_b = {r.get("person_id") for r in prev_f if r.get("line_type") == "product"}
    okb = len({r.get("person_id") for r in clients if r.get("is_client") == "Y"
               and (not filial_id or r.get("filial_id") == filial_id)})
    crev = defaultdict(float); cords = defaultdict(set); cnames = {}
    for r in cur_f:
        if r.get("line_type") != "product": continue
        pid = r.get("person_id",""); amt = to_uzs(sf(r.get("sold_amount")), r.get("currency_code","860"), rate)
        crev[pid] += amt; cords[pid].add(r.get("deal_id")); cnames[pid] = r.get("person_name","")
    def cv(v): return fmtr(to_disp(v, currency, rate))
    top = sorted([{"person_id": p, "name": cnames.get(p,p), "revenue": cv(v), "orders": len(cords[p])}
                  for p, v in crev.items()], key=lambda x: -x["revenue"])[:20]
    return {"okb": okb, "akb": len(cur_b), "akb_prev": len(prev_b),
            "new_clients": len(cur_b - prev_b), "lost_clients": len(prev_b - cur_b),
            "top_clients": top, "currency": currency}
