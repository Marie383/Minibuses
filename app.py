# %pip install streamlit pandas unidecode
import re, math, pandas as pd, streamlit as st
from unidecode import unidecode

# ==== CONFIGURACIÓN ====
# Pegá acá TU URL publicada como CSV (o una ruta local a .csv)
SHEET_CSV = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQLymjfowXd0_rC_1or1-icSAgPgMY0kQCCrh2CS34-xN0OWAGH-Pqwnh9FJ61g57iPOVlsIo60Qr7s/pub?output=csv"

COL_ORIGEN_NOMBRE   = "LOCALIDAD DE ORIGEN"
COL_ORIGEN_COORDS   = "COORDENADAS ORIGEN"
COL_DESTINO_NOMBRE  = "LOCALIDAD DE DESTINO"
COL_DESTINO_COORDS  = "COORDENADAS DESTINO"
COL_PARADAS         = "PARADAS INTERMEDIAS"
COL_DIAS            = "DIAS OPERATIVOS"
COL_OBS             = "OBSERVACIONES"

# ==== Parsers ====
dec_pair_re = re.compile(r"\s*([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)\s*")
dms_pair_re = re.compile(r"""
 (?P<lat_deg>-?\d+(?:\.\d+)?)\D*?(?P<lat_min>\d+(?:\.\d+)?)?\D*?(?P<lat_sec>\d+(?:\.\d+)?)?\s*(?P<lat_hem>[NnSs])\s+
 (?P<lon_deg>-?\d+(?:\.\d+)?)\D*?(?P<lon_min>\d+(?:\.\d+)?)?\D*?(?P<lon_sec>\d+(?:\.\d+)?)?\s*(?P<lon_hem>[EeWw])
""", re.VERBOSE)

def dms_to_decimal(d,m=0,s=0,h="N"):
    val = abs(float(d)) + float(m or 0)/60 + float(s or 0)/3600
    return -val if h.upper() in ("S","W") else val

def parse_latlon(s:str):
    if not isinstance(s,str): return None
    s = s.strip()
    m = dec_pair_re.match(s)
    if m: return float(m.group(1)), float(m.group(2))
    m = dms_pair_re.search(s)
    if m:
        lat = dms_to_decimal(m["lat_deg"], m["lat_min"], m["lat_sec"], m["lat_hem"])
        lon = dms_to_decimal(m["lon_deg"], m["lon_min"], m["lon_sec"], m["lon_hem"])
        return lat, lon
    return None

def norm(s):
    try: return unidecode(str(s)).strip().lower()
    except Exception: return str(s).strip().lower()

def split_intermedias(s: str):
    if not isinstance(s,str) or not s.strip(): return []
    items = re.split(r"[;,|]\s*", s.strip())
    out = []
    for it in items:
        if "@" in it:
            name, coords = it.split("@",1)
            c = parse_latlon(coords)
            out.append({"name": name.strip(), "coords": c})
        else:
            out.append({"name": it.strip(), "coords": None})
    return out

def hav_km(a,b,c,d):
    R=6371.0
    p1,p2=math.radians(a),math.radians(c)
    dphi=math.radians(c-a); dl=math.radians(d-b)
    x=math.sin(dphi/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(x))

# ==== Datos ====
@st.cache_data(show_spinner=False)
def load_data(src):
    df = pd.read_csv(src, encoding="utf-8-sig")
    df["_origen_latlon"] = df[COL_ORIGEN_COORDS].apply(parse_latlon)
    df["_dest_latlon"]   = df[COL_DESTINO_COORDS].apply(parse_latlon)
    df["_dest_norm"]     = df[COL_DESTINO_NOMBRE].apply(norm)
    df["_stops"]         = df[COL_PARADAS].apply(split_intermedias)
    return df

def elegir_por_servicio(df, origen_input, destino_input, max_rows=7):
    dest_key = norm(destino_input)
    sub = df[df["_dest_norm"].str.contains(dest_key, na=False)].copy()
    if sub.empty:
        return pd.DataFrame(), "No encontré servicios que lleguen a ese destino."

    # Coordenadas del usuario: cabecera con mismo nombre o lat,lon/DMS ingresado
    user_latlon = None
    ox = df[df[COL_ORIGEN_NOMBRE].apply(norm) == norm(origen_input)]
    if not ox.empty and isinstance(ox.iloc[0]["_origen_latlon"], tuple):
        user_latlon = ox.iloc[0]["_origen_latlon"]
    if user_latlon is None:
        user_latlon = parse_latlon(origen_input)

    cands = []
    for idx, r in sub.iterrows():
        if isinstance(r["_origen_latlon"], tuple):
            cands.append({
                "_row": idx, "Servicio hacia": r[COL_DESTINO_NOMBRE],
                "Parada": r[COL_ORIGEN_NOMBRE], "Tipo": "cabecera",
                "Lat": r["_origen_latlon"][0], "Lon": r["_origen_latlon"][1],
                "Días": r[COL_DIAS], "Obs": r.get(COL_OBS, "")
            })
        for s in r["_stops"]:
            cands.append({
                "_row": idx, "Servicio hacia": r[COL_DESTINO_NOMBRE],
                "Parada": s["name"] or "(sin nombre)", "Tipo": "intermedia",
                "Lat": s["coords"][0] if s["coords"] else None,
                "Lon": s["coords"][1] if s["coords"] else None,
                "Días": r[COL_DIAS], "Obs": r.get(COL_OBS, "")
            })
    cand = pd.DataFrame(cands)
    if user_latlon:
        ulat, ulon = user_latlon
        cand["Distancia (km)"] = cand.apply(
            lambda z: hav_km(ulat, ulon, z["Lat"], z["Lon"]) if pd.notna(z["Lat"]) and pd.notna(z["Lon"]) else None, axis=1
        )
    else:
        cand["Distancia (km)"] = None

    best = []
    for rid, g in cand.groupby("_row"):
        known = g[g["Distancia (km)"].notna()].sort_values("Distancia (km)")
        if not known.empty:
            best.append(known.iloc[0]); continue
        match_name = g[g["Parada"].apply(lambda x: norm(origen_input) in norm(x))]
        best.append((match_name.iloc[0] if not match_name.empty
                    else g[g["Tipo"]=="cabecera"].iloc[0] if not g[g["Tipo"]=="cabecera"].empty
                    else g.iloc[0]))
    out = pd.DataFrame(best).drop(columns=["_row"])
    if "Distancia (km)" in out.columns:
        out["Distancia (km)"] = out["Distancia (km)"].apply(lambda x: None if pd.isna(x) else round(float(x),1))
    out = out.sort_values(by=["Distancia (km)", "Parada"], na_position="last").head(max_rows)
    return out, None

# ==== UI ====
st.set_page_config(page_title="Minibus SF – Consulta", page_icon="🚌", layout="centered")
st.title("🚌 Consulta de minibuses – Provincia de Santa Fe")

with st.expander("Fuente de datos", expanded=False):
    SHEET_CSV = st.text_input("URL publicada como CSV (o ruta local a .csv)", value=SHEET_CSV)

try:
    df = load_data(SHEET_CSV)
except Exception as e:
    st.error(f"Error al cargar datos: {e}")
    st.stop()

c1,c2 = st.columns(2)
with c1:
    origen = st.text_input("Localidad de origen (o coordenadas)", placeholder="Ataliva  /  -30.94,-61.31  /  31°14'39.2\"S 61°30'02.7\"W")
with c2:
    destino = st.text_input("Destino", placeholder="Santa Fe / CEMAFE")

max_rows = st.slider("Máx. opciones a mostrar", 1, 15, 7)

if st.button("Buscar", type="primary", use_container_width=True):
    if not origen or not destino:
        st.warning("Completá origen y destino."); st.stop()
    res, err = elegir_por_servicio(df, origen, destino, max_rows)
    if err: st.error(err); st.stop()
    st.subheader("Resultados")
    st.dataframe(res[["Servicio hacia","Parada","Tipo","Distancia (km)","Días","Obs"]], use_container_width=True)

    st.subheader("Texto para derivación")
    filas = [f"- Parada: {r['Parada']} | Dist: {r['Distancia (km)'] or 's/d'} km | Días: {r['Días']} | Obs: {r['Obs']}" for _, r in res.iterrows()]
    st.code(f"Paciente en {origen}. Destino: {destino}.\nOpciones de minibús:\n" + "\n".join(filas), language="markdown")
