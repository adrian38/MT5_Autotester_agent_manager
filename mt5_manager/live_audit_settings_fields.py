"""Los campos que una pantalla de lectura cambia de un uso, uno a uno.

`live_audit_settings.py` pasó de 600 líneas al añadir los tramos de lote, y
éstos son el grupo que se puede separar entero: todos escriben **un solo
campo** de **un solo uso**, por el mismo motivo —quien los cambia está
mirando un resultado y no conoce el resto de la configuración, así que un
guardado completo borraría lo que esa pantalla no tenía que enviar—.

Van como mixin y no como funciones sueltas porque comparten el candado, el
mapa en memoria y el fichero del almacén, igual que `LiveAuditAccountsMixin`.
"""
from __future__ import annotations

from typing import Any, Callable

from .common import save_json, utc_now
from .live_audit_attribution import (
    empty_attribution,
    merge_attribution,
    remove_source,
    rewrite_profile_attribution,
)
from .live_audit_lot_schedule import normalize_lot_schedule
from .live_audit_comment_links import normalize_comment_links
from .live_audit_manual_pairs import normalize_manual_pairs
from .live_audit_settings_schema import (
    normalize_reviewed_history_strategies,
    normalize_reviewed_strategies,
    normalize_reviewed_unpaired_tester,
    normalize_view_lot_overrides,
)


class LiveAuditProfileFieldsMixin:
    """Lectura y escritura campo a campo del perfil de un uso."""

    def _set_profile_field(
        self, node_id: str, audit_id: str, field: str, value: Any,
    ) -> Any:
        """Persiste un campo de un solo uso, y sólo ese campo.

        Quien cambia uno de estos campos está en una pantalla de lectura y no
        conoce el resto de la configuración del uso: un guardado completo
        borraría lo que esa pantalla no tenía que enviar.
        """
        with self.lock:
            record = self.records.get(node_id) or {}
            profiles = {
                str(key): dict(stored)
                for key, stored in (record.get("profiles") or {}).items()
            }
            if str(audit_id) not in profiles:
                raise KeyError(f"El uso {audit_id} no tiene configuración guardada")
            profiles[str(audit_id)][field] = value
            self.records[node_id] = {**record, "profiles": profiles, "updated_at": utc_now()}
            save_json(self.path, self.records)
        return value

    def view_lot_overrides(self, node_id: str, audit_id: str) -> dict[str, Any]:
        """La reconciliación de lotes guardada para un uso."""
        with self.lock:
            profiles = (self.records.get(node_id) or {}).get("profiles") or {}
            stored = (profiles.get(str(audit_id)) or {}).get("view_lot_overrides")
        return normalize_view_lot_overrides(stored)

    def set_view_lot_overrides(
        self, node_id: str, audit_id: str, overrides: Any,
    ) -> dict[str, Any]:
        """Guarda cómo hay que escalar las curvas de un uso al leerlas.

        No es `real_strategy_lots`, que reanaliza y cambia el veredicto: esto
        es la vista. Vive en el perfil y no en `localStorage` porque si no, la
        portada y Análisis enseñan números distintos en cada dispositivo. Toca
        **un solo campo** de **un solo uso**, por lo mismo que la atribución:
        quien lo cambia no conoce el resto de la configuración.
        """
        return self._set_profile_field(
            node_id, audit_id, "view_lot_overrides",
            normalize_view_lot_overrides(overrides),
        )

    def reviewed_strategies(self, node_id: str, audit_id: str) -> dict[str, Any]:
        """Las estrategias que el usuario ya dio por revisadas en un uso."""
        with self.lock:
            profiles = (self.records.get(node_id) or {}).get("profiles") or {}
            stored = (profiles.get(str(audit_id)) or {}).get("reviewed_strategies")
        return normalize_reviewed_strategies(stored)

    def set_reviewed_strategies(
        self, node_id: str, audit_id: str, reviewed: Any,
    ) -> dict[str, Any]:
        """Guarda qué estrategias de un uso ya miró el usuario.

        No cambia el veredicto ni reanaliza nada: lo que cambia es la portada,
        que deja de enseñar lo ya revisado. Toca **un solo campo** de **un solo
        uso**, igual que el escalado de la vista: quien lo cambia está en
        Análisis y no conoce el resto de la configuración.
        """
        return self._set_profile_field(
            node_id, audit_id, "reviewed_strategies",
            normalize_reviewed_strategies(reviewed),
        )

    def reviewed_history_strategies(self, node_id: str, audit_id: str) -> dict[str, Any]:
        """Las estrategias que el usuario ya dio por revisadas en la serie."""
        with self.lock:
            profiles = (self.records.get(node_id) or {}).get("profiles") or {}
            stored = (profiles.get(str(audit_id)) or {}).get("reviewed_history_strategies")
        return normalize_reviewed_history_strategies(stored)

    def set_reviewed_history_strategies(
        self, node_id: str, audit_id: str, reviewed: Any,
    ) -> dict[str, Any]:
        """Guarda qué estrategias de la serie archivada ya miró el usuario.

        Mismo contrato que las del periodo y campo distinto, porque las dos
        tablas contestan preguntas distintas: validar «esta semana» no puede
        validar «todas las auditorías que llevamos».
        """
        return self._set_profile_field(
            node_id, audit_id, "reviewed_history_strategies",
            normalize_reviewed_history_strategies(reviewed),
        )

    def reviewed_unpaired_tester(self, node_id: str, audit_id: str) -> dict[str, Any]:
        """Las ejecutadas en demo sin real que el usuario ya miró, una a una."""
        with self.lock:
            profiles = (self.records.get(node_id) or {}).get("profiles") or {}
            stored = (profiles.get(str(audit_id)) or {}).get("reviewed_unpaired_tester")
        return normalize_reviewed_unpaired_tester(stored)

    def set_reviewed_unpaired_tester(
        self, node_id: str, audit_id: str, reviewed: Any,
    ) -> dict[str, Any]:
        """Guarda qué filas «SIN REAL» de un uso ya miró el usuario.

        Mismo contrato que las estrategias validadas, y por eso la misma
        puerta: no reanaliza nada, no cambia la comparación y lo único que
        cambia es que la portada deja de pedirlas. No es `manual_pairs`, que
        sí mueve el veredicto porque le pone real a la fila.
        """
        return self._set_profile_field(
            node_id, audit_id, "reviewed_unpaired_tester",
            normalize_reviewed_unpaired_tester(reviewed),
        )

    def manual_pairs(self, node_id: str, audit_id: str) -> dict[str, Any]:
        """Las parejas tester–real que el usuario decidió a mano en un uso."""
        with self.lock:
            profiles = (self.records.get(node_id) or {}).get("profiles") or {}
            stored = (profiles.get(str(audit_id)) or {}).get("manual_pairs")
        return normalize_manual_pairs(stored)

    def set_manual_pairs(
        self, node_id: str, audit_id: str, pairs: Any,
    ) -> dict[str, Any]:
        """Guarda qué real le toca a qué operación del tester.

        A diferencia del escalado de la vista y de las estrategias validadas,
        esto **sí** cambia el veredicto: la fila deja de ser «SIN REAL» y pasa a
        medirse con las tolerancias de siempre. Por eso quien la guarda vuelve a
        pedir el análisis en la misma llamada.
        """
        return self._set_profile_field(
            node_id, audit_id, "manual_pairs", normalize_manual_pairs(pairs),
        )

    def comment_links(self, node_id: str, audit_id: str) -> dict[str, Any]:
        """La tabla «comentario del EA → `.set`» guardada en un uso."""
        with self.lock:
            profiles = (self.records.get(node_id) or {}).get("profiles") or {}
            stored = (profiles.get(str(audit_id)) or {}).get("comment_links")
        return normalize_comment_links(stored)

    def set_comment_links(
        self, node_id: str, audit_id: str, links: Any,
    ) -> dict[str, Any]:
        """Guarda la tabla, o la retira si llega vacía.

        Sustituye y no acumula, al revés que la atribución: es un solo
        documento, el usuario lo mantiene entero y subir la versión corregida
        tiene que poder quitar una fila, no sólo añadirlas. Sí cambia el
        veredicto, así que quien la guarda vuelve a pedir el análisis en la
        misma llamada.
        """
        return self._set_profile_field(
            node_id, audit_id, "comment_links", normalize_comment_links(links),
        )

    def real_lot_schedule(self, node_id: str, audit_id: str) -> dict[str, Any]:
        """Los lotes que el usuario declaró vigentes desde una fecha."""
        with self.lock:
            profiles = (self.records.get(node_id) or {}).get("profiles") or {}
            stored = (profiles.get(str(audit_id)) or {}).get("real_lot_schedule")
        return normalize_lot_schedule(stored)

    def set_real_lot_schedule(
        self, node_id: str, audit_id: str, schedule: Any,
    ) -> dict[str, Any]:
        """Guarda desde cuándo rige cada lote de la cuenta real.

        Escribe **un solo campo**, y a propósito no es `real_strategy_lots`:
        aquel describe el lote de siempre y lo teclea quien configura el uso.
        Esto lo declara quien está mirando el resultado, que no conoce el resto
        de la configuración y no tiene por qué borrar el lote anterior para
        añadir el nuevo. Sí cambia el veredicto, así que quien lo guarda vuelve
        a pedir el análisis en la misma llamada.
        """
        return self._set_profile_field(
            node_id, audit_id, "real_lot_schedule", normalize_lot_schedule(schedule),
        )

    def _rewrite_attribution(
        self, node_id: str, audit_id: str, rewrite: Callable[[Any], dict[str, Any]],
    ) -> dict[str, Any]:
        """Persiste la atribución reescrita de un uso, y sólo ese campo."""
        with self.lock:
            record = self.records.get(node_id) or {}
            profiles = rewrite_profile_attribution(record, audit_id, rewrite)
            self.records[node_id] = {**record, "profiles": profiles, "updated_at": utc_now()}
            save_json(self.path, self.records)
            return dict(profiles[str(audit_id)]["real_attribution"])

    def set_real_attribution(
        self, node_id: str, audit_id: str, attribution: dict[str, Any],
    ) -> dict[str, Any]:
        """Acumula la atribución manual de un uso.

        Decidir de quién es cada real es trabajo manual que no se repite, así
        que un Excel nuevo se suma al anterior en vez de sustituirlo, y lo dicho
        en auditorías pasadas sigue ahí.
        """
        return self._rewrite_attribution(
            node_id, audit_id, lambda stored: merge_attribution(stored, attribution),
        )

    def set_real_attribution_with_manual_pairs(
        self, node_id: str, audit_id: str, attribution: dict[str, Any], pairs: Any,
    ) -> dict[str, Any]:
        """Guarda en una escritura las dos decisiones que venían en el Excel."""
        normalized_pairs = normalize_manual_pairs(pairs)
        with self.lock:
            record = self.records.get(node_id) or {}
            profiles = rewrite_profile_attribution(
                record, audit_id, lambda stored: merge_attribution(stored, attribution),
            )
            profiles[str(audit_id)]["manual_pairs"] = normalized_pairs
            self.records[node_id] = {**record, "profiles": profiles, "updated_at": utc_now()}
            save_json(self.path, self.records)
            return dict(profiles[str(audit_id)])

    def drop_real_attribution(
        self, node_id: str, audit_id: str, sha256: str | None,
    ) -> dict[str, Any]:
        """Retira un Excel de atribución, o todos si no se nombra ninguno."""
        return self._rewrite_attribution(
            node_id, audit_id,
            lambda stored: (
                remove_source(stored, sha256) if sha256 is not None else empty_attribution()
            ),
        )
