#!/usr/bin/env python3
"""
Costos por SKU: se leen del sistema de Contabilidad de Suprabond, no se
cargan a mano en esta app.

    python costos_gsu.py        -> cuantos SKU de ML tienen costo

**Donde viven de verdad.** Los costos NO estan en Contabilium: de los 596
productos del ERP, solo 3 tienen `CostoInterno` cargado (medido jul 2026, el
0,5%). La verdad vive en la hoja `costos_historico` de la Google Sheet del
proyecto *Contabilidad - Claude*, que se valida contra el catalogo de
Contabilium al cargarse. Este modulo lee esa hoja.

**La hoja es append-only y con vigencia.** Cada carga agrega un lote de filas
en vez de pisar las anteriores, y cada fila trae `fecha_vigencia_desde`. El
costo que vale hoy es el mas nuevo cuya vigencia ya empezo, asi que hay que
resolverlo por SKU: no alcanza con leer la ultima fila del archivo.

**Los costos son netos SIN IVA**, en las mismas unidades que `PrecioFinal/1.22`
de Contabilium. Los precios de MercadoLibre vienen CON IVA, asi que las
pantallas descuentan el 22% antes de comparar. Si algun dia se cambian a
costos con IVA hay que poner el selector de IVA en "Sin descontar".

Cobertura al 31/07/2026: 266 de los 293 SKU activos en ML tienen costo (91%).

Configuracion, en secrets:

    [gsheets_costos]
    spreadsheet_id = "el-id-de-la-planilla-de-Contabilidad"
    # en local:
    service_account_json_path = ".gsheets/sa.json"
    # en la nube, pegar el JSON del service account:
    # [gsheets_costos.service_account]
    # ...

Si no esta configurado, `traer_costos()` devuelve vacio y las pantallas siguen
andando pidiendo la planilla a mano, igual que antes.
"""

import sys
from datetime import date

import pandas as pd

import almacen

HOJA_COSTOS = "costos_historico"
COLUMNAS = ["sku", "costo", "fecha_vigencia_desde", "fecha_carga",
            "usuario", "nota"]


class CostosError(RuntimeError):
    pass


def _config():
    return almacen._seccion("gsheets_costos")


def configurado():
    cfg = _config()
    return bool(cfg.get("spreadsheet_id") and
                (cfg.get("service_account") or
                 cfg.get("service_account_json_path")))


def _abrir():
    """
    Abre la planilla de Contabilidad. Es una planilla DISTINTA de la que usa
    `almacen` para tokens y auditoria, por eso no se puede reusar `_abrir()`
    de ahi: aquella lee la seccion [gsheets] y esta la [gsheets_costos].
    """
    cfg = _config()
    if not configurado():
        raise CostosError(
            "Falta la seccion [gsheets_costos] en los secrets: sin eso no se "
            "pueden leer los costos del sistema de Contabilidad.")
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        alcance = ["https://www.googleapis.com/auth/spreadsheets",
                   "https://www.googleapis.com/auth/drive"]
        if cfg.get("service_account"):
            cred = Credentials.from_service_account_info(
                dict(cfg["service_account"]), scopes=alcance)
        else:
            cred = Credentials.from_service_account_file(
                cfg["service_account_json_path"], scopes=alcance)
        return gspread.authorize(cred).open_by_key(cfg["spreadsheet_id"])
    except Exception as e:
        raise CostosError(
            f"No pude abrir la planilla de costos ({cfg.get('spreadsheet_id')}). "
            f"Verifica el ID y que este compartida con el client_email del "
            f"service account. Detalle: {e}") from e


def _a_numero(v):
    """Tolera '1.234,56' y '$ 1234' como el resto del proyecto."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace("$", "").replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def leer_historico():
    """El histórico completo, sin resolver vigencias. DataFrame vacio si no hay."""
    if not configurado():
        return pd.DataFrame(columns=COLUMNAS)
    hoja = _abrir().worksheet(HOJA_COSTOS)
    filas = hoja.get_all_records()
    df = pd.DataFrame(filas)
    if not len(df):
        return pd.DataFrame(columns=COLUMNAS)
    for c in COLUMNAS:
        if c not in df.columns:
            df[c] = ""
    df["sku"] = df["sku"].astype(str).str.strip().str.upper()
    df["costo"] = df["costo"].map(_a_numero)
    return df[COLUMNAS]


def vigentes(df=None, hasta=None):
    """
    SKU -> costo que rige en `hasta` (por defecto hoy).

    Se queda con la fila de vigencia mas reciente que ya empezo. Las filas con
    vigencia futura se ignoran a proposito: sirven para dejar cargado un costo
    que todavia no entro.
    """
    if df is None:
        df = leer_historico()
    if not len(df):
        return {}
    hasta = hasta or date.today().isoformat()

    d = df[df["costo"].notna()].copy()
    d["fecha_vigencia_desde"] = d["fecha_vigencia_desde"].astype(str)
    d = d[d["fecha_vigencia_desde"] <= str(hasta)]
    if not len(d):
        return {}
    d = d.sort_values("fecha_vigencia_desde")
    return dict(zip(d["sku"], d["costo"]))     # el ultimo de cada SKU gana


def traer_costos(hasta=None):
    """
    `(DataFrame sku/costo, de donde salio)`, en el formato que esperan
    Rentabilidad, Precio optimo y Buy Box.

    Nunca lanza: si la planilla no esta configurada o falla la lectura,
    devuelve vacio para que las pantallas sigan funcionando pidiendo el
    archivo a mano.
    """
    try:
        m = vigentes(hasta=hasta)
    except Exception as e:  # noqa: BLE001
        return pd.DataFrame(columns=["sku", "costo"]), f"no se pudo leer: {e}"
    if not m:
        return pd.DataFrame(columns=["sku", "costo"]), ""
    df = pd.DataFrame({"sku": list(m), "costo": list(m.values())})
    return df, f"sistema de Contabilidad · {len(df)} SKU"


def cobertura(pubs=None):
    """Cuantos SKU activos de ML tienen costo. Para el diagnostico del CLI."""
    import json
    from pathlib import Path

    from catalogo import sku_del_atributo

    if pubs is None:
        pubs = json.loads(
            (Path(__file__).resolve().parent / "catalogo.json")
            .read_text(encoding="utf-8"))
    activos = {(sku_del_atributo(p) or "").strip().upper()
               for p in pubs if p.get("status") == "active"}
    activos.discard("")
    con = vigentes()
    tienen = {s for s in activos if s in con}
    return activos, tienen


def main():
    if not configurado():
        print("Falta [gsheets_costos] en los secrets. Ver el docstring.")
        return 1
    df = leer_historico()
    print(f"Historico: {len(df)} filas")
    m = vigentes(df)
    print(f"SKU con costo vigente hoy: {len(m)}")

    activos, tienen = cobertura()
    print(f"\nSKU activos en MercadoLibre: {len(activos)}")
    print(f"  con costo : {len(tienen):>4}  ({len(tienen)/len(activos):.0%})")
    print(f"  sin costo : {len(activos - tienen):>4}")
    faltan = sorted(activos - tienen)
    if faltan:
        print("\n  Primeros sin costo:", ", ".join(faltan[:10]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CostosError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)
