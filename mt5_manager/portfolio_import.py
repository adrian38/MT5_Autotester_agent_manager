"""Importar un portafolio desde lo que ya produce la exportación.

El caso real: se exporta un portafolio, se borra del manager, y meses después
hace falta que sus sets **sigan contando como usados** para que la siguiente
generación no los repita. Sin importarlos, `exclude_used_sets` no los ve y el
optimizador vuelve a proponer las mismas estrategias.

## De dónde sale la información

De la carpeta de exportación tal y como la escribe hoy `export_portfolio`, sin
formato nuevo: los `.set` copiados y el `PORTAFOLIO_<id>_resumen.txt`, que trae
capital, DD objetivo y usado, net total y una fila por estrategia con perfil,
cuenta, símbolo, timeframe, unidades, lote y nombre del set. Eso vale para
exportaciones **ya hechas**, que es justo lo que hay cuando el portafolio ya se
borró.

## Por qué el resultado es un portafolio normal y no una copia degradada

Lo único que se reconstruye a mano es la **composición** (qué set, con cuántas
unidades, en qué variante). Todo lo demás —curva, DD valle, DD puntual, aporte
por estrategia, flotante, bootstrap de estrés— se **recalcula** a partir de los
informes MT5 del candidato, que siguen en el proyecto del agente:
`load_robust_sets_from_rows` los vuelve a parsear y `evaluate_portfolio` los
evalúa con las mismas funciones que usa un cálculo nuevo. El resumen aporta la
composición; los números salen de los informes, no del texto.

Por eso la importación termina llamando a `save_proposal`, el mismo camino que
guarda una propuesta recién calculada: la fila resultante es indistinguible de
una guardada de forma normal, con sus variantes A/M/C, sus métricas y su
`metrics_json`.

## Lo que no puede traer

- **Margen**: la exportación no lo lleva y depende de la cuenta y de las specs
  del símbolo en el momento del cálculo. Queda a 0, igual que en un portafolio
  guardado por un nodo sin modelo de margen.
- **Registro de decisiones del optimizador**: es la historia de una búsqueda que
  aquí no ha ocurrido. La composición viene dada, no elegida.
- **Sets cuyo candidato ya no existe** en la memoria del agente (rechazado o
  borrado): no hay informes con los que reconstruirlos. Se nombran en el informe
  de importación en vez de desaparecer en silencio.
"""
from __future__ import annotations

import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUMMARY_PATTERN = "PORTAFOLIO_*_resumen.txt"
TABLE_HEADER = "PERFIL"

#: Cabecera del resumen -> clave interna. El texto lo escribe `export_portfolio`.
HEADER_KEYS = {
    "portafolio": "name",
    "tipo": "portfolio_type",
    "capital": "capital",
    "dd valle objetivo": "target_valley_dd",
    "dd puntual objetivo": "target_point_dd",
    "dd valle usado": "actual_valley_dd",
    "dd puntual usado": "actual_point_dd",
    "net profit total 2020-2026": "total_net_profit",
}


@dataclass
class ImportedMember:
    """Una fila de la tabla del resumen."""

    variant_label: str
    account: str
    symbol: str
    timeframe: str
    units: int
    lot: float
    set_name: str


class ImportError_(ValueError):
    """Error de importación con mensaje para el usuario."""


def _number(value: Any) -> float:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _split_row(line: str) -> list[str] | None:
    """Separa una fila de la tabla del resumen.

    `export_portfolio` la escribe con anchos fijos
    (``{perfil:12s} {cuenta:12s} {simbolo:12s} {tf:5s} {unid:7d} {lote:7.2f}   {set}``),
    así que el corte por posición es exacto. Pero el perfil y la cuenta se
    truncan a 12 y el símbolo no, de modo que un símbolo largo desplaza las
    columnas. Si el corte por posición no deja un nombre de set creíble se
    reparte por la derecha, donde el orden sí es fijo.
    """
    if len(line) > 63:
        columns = [
            line[0:12].strip(), line[13:25].strip(), line[26:38].strip(),
            line[39:44].strip(), line[45:52].strip(), line[53:60].strip(), line[63:].strip(),
        ]
        if columns[-1].lower().endswith(".set") and columns[4].isdigit():
            return columns
    tokens = line.split()
    if len(tokens) < 7 or not tokens[-1].lower().endswith(".set"):
        return None
    return [" ".join(tokens[:-6]), *tokens[-6:]]


def parse_summary(text: str) -> tuple[dict[str, Any], list[ImportedMember]]:
    header: dict[str, Any] = {}
    members: list[ImportedMember] = []
    in_table = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith(TABLE_HEADER):
            in_table = True
            continue
        if line.startswith("OMITIDOS"):
            break
        if in_table:
            columns = _split_row(line)
            if columns is None:
                continue
            members.append(ImportedMember(
                variant_label=columns[0], account=columns[1], symbol=columns[2],
                timeframe=columns[3], units=int(_number(columns[4])), lot=_number(columns[5]),
                set_name=columns[6],
            ))
            continue
        # La cabecera admite dos pares en la misma linea: «Tipo: X   Capital: Y».
        for chunk in re.split(r"\s{3,}", line):
            if ":" not in chunk:
                continue
            label, _, value = chunk.partition(":")
            key = HEADER_KEYS.get(label.strip().lower())
            if not key:
                continue
            header[key] = value.strip() if key in {"name", "portfolio_type"} else _number(value)
    return header, members


def read_export(path: str | Path) -> tuple[dict[str, Any], list[ImportedMember], list[str]]:
    """Lee una carpeta de exportación o su ZIP y devuelve lo que contiene."""
    source = Path(str(path)).expanduser()
    if not source.exists():
        raise ImportError_(f"No existe la ruta indicada: {source}")
    if source.is_file():
        if source.suffix.lower() != ".zip":
            raise ImportError_("Selecciona la carpeta exportada o su archivo ZIP")
        with tempfile.TemporaryDirectory(prefix="mt5-portfolio-import-") as temp_dir:
            with zipfile.ZipFile(source) as archive:
                archive.extractall(temp_dir)
            return _read_folder(Path(temp_dir))
    return _read_folder(source)


def _read_folder(root: Path) -> tuple[dict[str, Any], list[ImportedMember], list[str]]:
    summaries = sorted(root.rglob(SUMMARY_PATTERN))
    set_files = sorted({path.name for path in root.rglob("*.set")})
    if not summaries:
        raise ImportError_(
            "La carpeta no contiene ningún PORTAFOLIO_*_resumen.txt: no parece una "
            "exportación de portafolio del manager"
        )
    if len(summaries) > 1:
        raise ImportError_(
            "La carpeta contiene varias exportaciones. Importa una cada vez: "
            + ", ".join(path.name for path in summaries)
        )
    header, members = parse_summary(summaries[0].read_text(encoding="utf-8", errors="replace"))
    if not members:
        raise ImportError_("El resumen no lista ninguna estrategia")
    return header, members, set_files


def variant_key_for(label: str, order: list[str]) -> str:
    """Traduce el perfil del resumen a la clave de variante.

    El perfil se escribe truncado a 12 caracteres, así que «Moderado Grid» llega
    como «Moderado Gri»: la comparación es por prefijo, no por igualdad. Un
    perfil que no reconozca ninguna etiqueta conocida conserva su orden de
    aparición, que es lo único fiable que queda.
    """
    from .portfolio_service import TYPE_LABELS

    normalized = label.strip().lower()
    for key, text in TYPE_LABELS.items():
        if normalized.startswith(text.lower()[: len(normalized)]) and normalized:
            return key
    for key in ("profit", "balanced", "margin"):
        if normalized.startswith(key):
            return key
    return f"variant_{order.index(label) + 1}"
