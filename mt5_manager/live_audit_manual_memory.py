"""Los reportes manuales del usuario, guardados por el manager y no por el nodo.

El nodo guarda **una** ejecución por uso: la siguiente sustituye `last_payload`
y con él la evidencia de que ese `.set` ya se había corregido a mano. El HTML
sigue en la carpeta de su ejecución, pero el agente **sólo sirve artefactos de
la ejecución visible** (`artifact_path`), así que en cuanto el cron ejecuta otra
vez, ese fichero ya no se puede ni pedir: la pantalla vuelve a reclamarlo y el
trabajo del usuario se tira cada noche.

Por eso el manager se queda con su copia al reenviar la carga, igual que el
diario de ejecuciones guarda lo que ya no cabe en el nodo. Una entrada por
`nodo/uso/.set` con el HTML, su SHA-256 y **la ventana auditada entonces**.

La ventana es lo que decide si el reporte sirve para la ejecución siguiente. Un
backtest al 100 % del 1 al 12 de octubre no contiene ni una operación del 12 al
20: el nodo lo aplicaría igual —no mira fechas— y el veredicto compararía otros
días. Heredar sólo con la misma ventana no es una restricción de más; es la
diferencia entre conservar el trabajo y falsear la comparación.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import threading
from pathlib import Path
from typing import Any

from .common import load_json, save_json
from .live_audit_archive import _slug

MANUAL_SOURCE = "manual_upload"
"""Lo que el nodo escribe en `report_source` al aceptar una carga manual."""


def canonical_name(source_set: Any) -> str:
    """El nombre exacto que el nodo exige para sustituir ese `.set`."""
    stem = Path(str(source_set or "")).stem
    return f"{stem}.htm" if stem else ""


def uploaded_contents(body: Any) -> dict[str, bytes]:
    """Los HTML que el navegador acaba de mandar, por nombre canónico.

    El nodo compara los nombres sin distinguir mayúsculas y acepta `.html`, así
    que la copia del manager se guarda bajo la misma forma canónica que luego
    reenviará: la del `.set`.
    """
    contents: dict[str, bytes] = {}
    for item in (body or {}).get("reports") or [] if isinstance(body, dict) else []:
        name = canonical_name(item.get("filename")) if isinstance(item, dict) else ""
        if not name:
            continue
        try:
            contents[name.casefold()] = base64.b64decode(
                str(item.get("content_base64") or ""), validate=True,
            )
        except (ValueError, binascii.Error):
            continue
    return contents


def window(payload: dict[str, Any]) -> tuple[str, str]:
    """La ventana auditada, tal y como la publica el nodo."""
    return str(payload.get("period_start") or ""), str(payload.get("period_end") or "")


class ManualReportMemory:
    """Persistente: un reinicio del manager no puede olvidar la corrección."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.lock = threading.RLock()
        self.records: dict[str, dict[str, Any]] = {}
        index = self.directory / "index.json"
        if index.is_file():
            try:
                stored = load_json(index)
            except ValueError:
                stored = {}
            self.records = {
                str(key): dict(value)
                for key, value in (stored.get("reports") or {}).items()
                if isinstance(value, dict)
            }

    @staticmethod
    def key(node_id: str, audit_key: str, source_set: str) -> str:
        return f"{node_id}/{audit_key}/{source_set}"

    def _report_file(self, node_id: str, audit_key: str, source_set: str) -> str:
        """Relativo al almacen a proposito: la ruta absoluta no es portable.

        El manager corre en Docker con `runtime/` montado, asi que un indice
        escrito desde fuera guardaria rutas de Windows que el contenedor no
        puede abrir -y al reves-.
        """
        name = f"{_slug(canonical_name(source_set))}.htm"
        return f"{_slug(node_id)}/{_slug(audit_key)}/{name}"

    def lookup(self, node_id: str, audit_key: str, source_set: str) -> dict[str, Any]:
        with self.lock:
            record = self.records.get(self.key(node_id, audit_key, source_set))
            return dict(record) if isinstance(record, dict) else {}

    def content(self, node_id: str, audit_key: str, source_set: str) -> bytes:
        """El HTML guardado, comprobando que es el mismo que se aceptó."""
        record = self.lookup(node_id, audit_key, source_set)
        path = self.directory / str(record.get("file") or "")
        if not record or not record.get("file") or not path.is_file():
            raise FileNotFoundError(f"No hay reporte manual guardado para {source_set}")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != str(record.get("sha256") or ""):
            raise ValueError(f"El reporte manual guardado de {source_set} no coincide con su SHA-256")
        return content

    def remember(
        self, node_id: str, audit_key: str, payload: dict[str, Any],
        contents: dict[str, bytes] | None = None,
    ) -> list[str]:
        """Anota los reportes manuales de esta ejecución; guarda los que lleguen.

        `contents` son los bytes por nombre canónico, que sólo existen cuando
        la carga acaba de pasar por el manager. Sin ellos se refresca la ficha
        —ejecución y ventana nuevas— conservando el fichero ya guardado.
        """
        execution_id = str(payload.get("audit_id") or "")
        span = window(payload)
        if not execution_id or not all(span):
            return []
        remembered: list[str] = []
        with self.lock:
            for artifact in payload.get("strategy_artifacts") or []:
                source_set = str(artifact.get("source_set") or "") if isinstance(artifact, dict) else ""
                if not source_set or str(artifact.get("report_source") or "") != MANUAL_SOURCE:
                    continue
                if self._write(node_id, audit_key, source_set, artifact, execution_id, span, contents):
                    remembered.append(source_set)
            if remembered:
                save_json(self.directory / "index.json", {"reports": self.records})
        return remembered

    def _write(
        self, node_id: str, audit_key: str, source_set: str, artifact: dict[str, Any],
        execution_id: str, span: tuple[str, str], contents: dict[str, bytes] | None,
    ) -> bool:
        """Una ficha nueva; devuelve si quedó con fichero que heredar."""
        key = self.key(node_id, audit_key, source_set)
        previous = self.records.get(key) or {}
        content = (contents or {}).get(canonical_name(source_set).casefold())
        name = str(previous.get("file") or "") or self._report_file(node_id, audit_key, source_set)
        if content is not None:
            name = self._report_file(node_id, audit_key, source_set)
            path = self.directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        elif not (self.directory / name).is_file():
            return False
        self.records[key] = {
            "execution_id": execution_id, "file": name,
            "sha256": hashlib.sha256(content).hexdigest() if content is not None
            else str(previous.get("sha256") or ""),
            "original_file": str(artifact.get("manual_report_original_file") or ""),
            "uploaded_at": str(artifact.get("manual_report_uploaded_at") or ""),
            "period_start": span[0], "period_end": span[1],
        }
        return True

    def state(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return {key: dict(value) for key, value in self.records.items()}
