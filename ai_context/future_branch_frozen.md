# FUTURE está congelado

El usuario congeló la rama y el checkout FUTURE el **2026-10-21**. Esta regla
es deliberadamente más restrictiva que la autorización general para escribir
en todos los agentes.

Hasta que el usuario diga expresamente que FUTURE vuelve a estar habilitado:

- no editar ningún archivo de
  `C:\Users\13199\Adrian\TRADING\MT5_Autotester_agent_FUTURE`;
- no crear commits, portar cambios ni ajustar configuración en la rama
  `future`;
- no parar ni reiniciar su aplicación o su nodo embebido;
- sí se permite leer el checkout para diagnóstico, siempre que la operación no
  escriba en el árbol ni en su runtime.

Cuando una tarea pida cambiar «todos los agentes», FUTURE se omite y se declara
en la entrega como copia congelada. Los ports se limitan a IC, AXI y
RoboForex/`dev`.

El último cambio realizado antes de la congelación fue el port del sondeo de
cuentas reales, commit `121831a`. Ése es el punto de partida congelado. Sólo
una instrucción posterior y explícita del usuario levanta esta prohibición; no
se infiere de una petición general, de una corrección de paridad ni de que el
checkout sea técnicamente escribible.
