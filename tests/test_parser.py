from pathlib import Path

from telemetria.parser import cargar_csv, parse_linea, segmentar

MEDICIONES = Path(__file__).resolve().parents[2] / "mediciones"


def test_formato_heredado():
    m = parse_linea("ACC[g]: -0.01,0.01,0.98 | ANG[deg]: -0.7,0.5,-10.6", 12.5)
    assert (m.t, m.ax, m.ay, m.az) == (12.5, -0.01, 0.01, 0.98)
    assert (m.roll, m.pitch, m.yaw) == (-0.7, 0.5, -10.6)
    assert m.t_esp_ms is None and not m.vacia


def test_trama_en_ceros_es_vacia():
    m = parse_linea("ACC[g]: 0.00,0.00,0.00 | ANG[deg]: 0.0,0.0,0.0", 0)
    assert m.vacia


def test_formato_v2_con_bandera():
    m = parse_linea("123456,0.1,0.2,0.97,1.0,2.0,3.0,1", 999)
    assert m.t == 123.456 and m.t_esp_ms == 123456 and m.viejo
    m = parse_linea("10,0,0,1,0,0,0", 0)
    assert not m.viejo


def test_lineas_basura():
    assert parse_linea("", 0) is None
    assert parse_linea("hola", 0) is None
    assert parse_linea("ACC[g]: x,y | ANG", 0) is None


def test_csv_reales():
    for ruta in sorted(MEDICIONES.glob("*.csv")):
        muestras = cargar_csv(ruta)
        assert len(muestras) > 100
        assert all(b.t >= a.t for a, b in zip(muestras, muestras[1:]))


def test_segmentar_corta_huecos_y_vacias():
    mk = lambda t, v=1.0: parse_linea(f"ACC[g]: 0,0,{v} | ANG[deg]: 0,0,0", t)
    serie = [mk(0), mk(0.2), mk(0.4), mk(5.0), mk(5.2), mk(5.4, 0), mk(5.6)]
    serie[5] = parse_linea("ACC[g]: 0,0,0 | ANG[deg]: 0,0,0", 5.4)
    tramos = segmentar(serie)
    assert [len(t) for t in tramos] == [3, 2, 1]
