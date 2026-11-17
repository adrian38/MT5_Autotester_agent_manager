"""Los ajustes y las terminales que comparten las pruebas del nodo.

Viven aparte desde que la limpieza historica se llevo sus pruebas a
`test_node_cleanup.py`: las dos necesitan el mismo proyecto de mentira y
duplicar el `ui_settings.ini` habria dejado dos criterios donde hay uno.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

UI_SETTINGS = """[Paths]
set_files_root=C:\\sets
ubs_generation_output=C:\\output
template_path={template}
ubs_ex5_file=C:\\experts\\ubs.ex5
mt5_path=C:\\MT5\\terminal64.exe
mt5_data_root=C:\\MT5Data

[General]
delay=5
ubs_broker=ICTRADING
ubs_account_type=STANDARD
ubs_generation_count=2
ubs_variants_per_seed=10
ubs_max_seeds=30
ubs_agent_execute=1
ubs_generation_mode=production
ubs_pass_min_net_profit=100
ubs_pass_min_profit_factor=1.2
ubs_pass_min_trades=50
ubs_pass_max_drawdown_pct=25
ubs_pass_min_recovery_factor=1.0
ubs_long_tf_min_trades_w1=11
ubs_long_tf_min_trades_mn=4
ubs_robust_from_date=2025.01.01
ubs_robust_to_date=2025.12.31
ubs_robust_pass_min_net_profit=20
ubs_robust_pass_min_profit_factor=1.2
ubs_robust_pass_min_trades=40
ubs_robust_pass_max_drawdown_pct=25
ubs_robust_pass_min_recovery_factor=1.0
ubs_robust_positive_bonus=70
ubs_robust_negative_bonus=-70
ubs_final_tick_from_date=2026.01.01
ubs_final_tick_to_date=2026.01.31
ubs_final_tick_6m_from_date=2026.01.01
ubs_final_tick_6m_to_date=2026.06.30
ubs_final_tick_min_history_quality=80
ubs_final_tick_min_ohlc_trades=5
ubs_final_tick_min_trades_w1=2
ubs_final_tick_min_trades_mn=1
ubs_final_tick_max_net_delta_pct=35
ubs_final_tick_max_pf_delta_pct=35
ubs_final_tick_max_dd_delta_pct=35
ubs_final_tick_max_trades_delta_pct=35

[Multiterminal]
enabled=0
"""
"""Los ajustes que el nodo lee en cada prueba. `{template}` es lo unico que
cambia entre ejecuciones."""

TERMINALS = r"""
[Terminal.1]
enabled=1
broker=ROBOFOREX
name=MT5 principal
mt5_path=C:\Program Files\RoboForex MT5 Terminal\terminal64.exe
data_dir=C:\MT5\OTRO

[Terminal.2]
enabled=1
broker=ICTRADING
name=MT5_IC_1
mt5_path=C:\Program Files\IC\terminal64.exe
data_dir=C:\MT5\IC_1

[Terminal.3]
enabled=0
broker=ICTRADING
name=MT5_IC_2
mt5_path=C:\Program Files\IC\terminal64.exe
data_dir=C:\MT5\IC_2
"""
"""Las terminales tal y como las declara un agente de verdad.

El `[Terminal.1]` de RoboForex no esta de adorno: ese mismo lo llevan el
`ui_settings.ini` de IC y el de AXI, y es el que la limpieza no debe tocar."""


def write_project(case: object) -> None:
    """El proyecto minimo que el nodo espera, con su `ui_settings.ini`.

    Deja en el caso `temp`, `root` y `config`, que es lo que piden tanto
    los comandos de generacion como las etapas de limpieza.
    """
    case.temp = tempfile.TemporaryDirectory()
    case.root = Path(case.temp.name)
    (case.root / "ubs_agent.py").write_text("print('ok')" + chr(10), encoding="utf-8")
    (case.root / "tester_template.ini").write_text("[Tester]" + chr(10), encoding="utf-8")
    (case.root / "ui_settings.ini").write_text(
        UI_SETTINGS.format(template=case.root / "tester_template.ini"),
        encoding="utf-8",
    )
    case.config = {
        "node_id": "ic", "project_dir": str(case.root), "token": "secret",
        "broker": "ICTRADING", "account_type": "STANDARD", "python_executable": "python",
    }
