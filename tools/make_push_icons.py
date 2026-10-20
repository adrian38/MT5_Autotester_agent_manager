"""Genera los iconos del manifiesto: `python -m tools.make_push_icons`.

Hacen falta para que el manager se pueda instalar en la pantalla de inicio, y
eso hace falta para que iOS acepte avisos. Android los usa como icono del
aviso; sin ellos sale una campana gris generica.

Se generan con un script en vez de guardar un PNG dibujado a mano porque no
hay ninguna dependencia de imagen en el workspace -ni `Pillow` ni nada- y
porque asi el icono es reproducible: cambiar el color o el tamano es cambiar
una constante y volver a ejecutarlo, no abrir un editor.

Un PNG a pelo son cuatro trozos con su CRC. No se usa `struct.pack` para nada
mas que las longitudes, y el filtro de cada linea es 0 (sin filtrar): el
fichero sale algo mas grande y el codigo cabe de un vistazo.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "mt5_manager" / "static"

BACKGROUND = (9, 16, 25)
"""`--bg` de `styles.css`. Opaco y a sangre: un icono `maskable` lo recorta el
sistema con la forma que quiera, asi que no puede traer esquinas redondeadas
propias ni transparencia."""

BELL = (32, 201, 151)
"""`--accent`, el verde del manager."""

SIZES = {"icon-192.png": 192, "icon-512.png": 512, "icon-180.png": 180}
"""192 y 512 son los que pide Android para considerar la web instalable; 180 es
el `apple-touch-icon` del iPhone."""

SUPERSAMPLE = 3
"""Muestras por lado y pixel. Sin esto el borde de la campana sale a escalones,
que en 180 pixeles se ve."""


def _inside_bell(x: float, y: float) -> bool:
    """La campana, en fracciones del lado. El origen esta arriba a la izquierda.

    Son cuatro piezas: el asa, el cuerpo que se abre hacia abajo, el borde y el
    badajo. La potencia del cuerpo es lo que le da la curva; con exponente 1 se
    queda en un trapecio, y por encima de 1 sale una falda en vez de una
    campana: tiene que abrirse rapido en los hombros y casi nada al final.
    """
    dx = abs(x - 0.5)
    if (x - 0.5) ** 2 + (y - 0.235) ** 2 <= 0.048 ** 2:
        return True  # asa
    if 0.28 <= y <= 0.655:
        return dx <= 0.085 + 0.215 * ((y - 0.28) / 0.375) ** 0.62  # cuerpo
    if 0.655 < y <= 0.70:
        return dx <= 0.325  # borde
    return (x - 0.5) ** 2 + (y - 0.765) ** 2 <= 0.058 ** 2  # badajo


def _pixel(column: int, row: int, size: int) -> tuple[int, int, int]:
    hits = 0
    for sub_y in range(SUPERSAMPLE):
        for sub_x in range(SUPERSAMPLE):
            x = (column + (sub_x + 0.5) / SUPERSAMPLE) / size
            y = (row + (sub_y + 0.5) / SUPERSAMPLE) / size
            hits += _inside_bell(x, y)
    weight = hits / SUPERSAMPLE ** 2
    return tuple(
        round(back + (front - back) * weight)
        for back, front in zip(BACKGROUND, BELL)
    )


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def png(size: int) -> bytes:
    rows = bytearray()
    for row in range(size):
        rows.append(0)  # filtro «None» para esta linea
        for column in range(size):
            rows.extend(_pixel(column, row, size))
    return b"".join([
        b"\x89PNG\r\n\x1a\n",
        # 8 bits por canal, color tipo 2 (RGB sin alfa): el iPhone pinta un
        # icono con transparencia sobre negro y se vería un marco.
        _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)),
        _chunk(b"IDAT", zlib.compress(bytes(rows), 9)),
        _chunk(b"IEND", b""),
    ])


def main() -> int:
    for name, size in SIZES.items():
        path = STATIC / name
        path.write_bytes(png(size))
        print(f"{path.relative_to(STATIC.parents[1])}: {size}x{size}, {path.stat().st_size} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
