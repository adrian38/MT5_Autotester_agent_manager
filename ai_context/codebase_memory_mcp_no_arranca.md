# `codebase-memory-mcp` se queda en «connecting» y no da error

## Síntoma

El servidor MCP nunca termina de conectar. No hay error en la interfaz: Claude Code
lo deja indefinidamente en «connecting» porque el proceso muere antes de hablar
JSON-RPC, así que no hay nada que mostrar. `ToolSearch` no encuentra ninguna de
sus herramientas y el grafo no está disponible en sesión.

## Causa (2026-08-23)

Lanzando a mano lo que arrancaba `.mcp.json`:

```
codebase-memory-mcp: exact executable identity could not be verified (cache-private)
 - C:\Users\Adrian: DACL entry 4 grants mutation rights 0x000d0152 to untrusted
   identity (other S-1-5-21-3210962696-352396110-881286353-1003)
```

Sale con código 1. La versión **0.10.8** estrenó una comprobación de cadena de
suministro sobre su **caché privada**: se niega a arrancar si alguna carpeta por
encima de ella la puede escribir una identidad que no considera de confianza. Por
defecto la caché cuelga del perfil, y el perfil concede control total heredable a
dos cuentas locales además del dueño (`beatriz`, `test`), que podrían sustituirle
el ejecutable descargado.

El paquete npm se actualizó a 0.10.8 el **2026-08-21 16:45** (binario nativo del
19-08); hasta entonces era 0.9.0, que no comprueba nada. Ahí se rompió.

## Solución

Sacar caché y runtime del perfil. Es lo que ya hacían los proyectos de Idrica
(`dm-mejoras`, `dm-devicesimulator`), configurados el mismo día en `~/.claude.json`:

```json
"env": {
  "CBM_ALLOWED_ROOT": "<raíz del proyecto>",
  "CBM_CACHE_DIR": "C:\\cbm\\cache",
  "CBM_RUNTIME_DIR": "C:\\cbm\\run"
}
```

`C:\cbm` ya existe en este equipo desde esa fecha. Con esas dos variables la 0.10.8
arranca y responde `initialize`; sin ellas, muere. Comprobado el 2026-08-23.

**No hay que tocar los ACE del perfil.** El mensaje nombra `C:\Users\Adrian` porque
es donde estaba la caché, no porque el perfil tenga que cambiar. `icacls` sobre el
perfil es una vía equivocada y arriesgada: quita permisos que alguien concedió a
propósito y no es lo que arregla esto.

## Trampa de diagnóstico: hay dos binarios

`cmd /c codebase-memory-mcp` resuelve por PATH a
`C:\nvm4w\nodejs\codebase-memory-mcp.cmd`, que es el **npm 0.10.8**, no el exe que
documenta `CLAUDE.md` para el fallback por CLI:

| Ruta | Versión |
| --- | --- |
| `C:\nvm4w\nodejs\node_modules\codebase-memory-mcp\bin\codebase-memory-mcp.exe` | 0.10.8 |
| `C:\Users\Adrian\AppData\Local\Programs\codebase-memory-mcp\codebase-memory-mcp.exe` | 0.9.0 |

La 0.9.0 no lleva la comprobación, así que el CLI de `CLAUDE.md` seguía funcionando
mientras el servidor no conectaba. **Que el CLI vaya no demuestra que el servidor
arranque: son ejecutables distintos y versiones distintas.**

## Nota sobre `CBM_ALLOWED_ROOT`

Apuntaba a `I:\TRADING\MT5_Autotester_agent_manager`, unidad no montada en este
equipo. Es **inocuo** —el servidor lo ignora y usa el cwd— pero se corrigió a la
ruta real para que no despiste en el próximo diagnóstico.
