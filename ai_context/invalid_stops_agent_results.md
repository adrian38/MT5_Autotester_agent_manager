# Invalid stops in ICTrading Results (2026-09-06)

Run 445, candidates 82223/82224 (KNDI.NAS M30), showed `no_trades` despite
explicit MT5 `[Invalid stops]` failures. The previous fix only covered OOS
robustness and required a start header that the runner's 64 KiB journal tail
often truncates away.

Fixed in the authorized IC checkout:
`C:/Users/Adrian/Adrian/TRADING/MT5_Autotester_agent_IC/MT5_Autotester_agent`.
The shared detector lives in `ubs/tester_diagnostics.py`; base and seed
evaluation in `ubs_agent.py` now persist rejection evidence, as does the OOS
flow. `ubs/memory.py` handles latest-run recovery and `ui/ubs_*_logic.py`
renders the explicit reason. Details and verification are in that checkout's
`ai_context/invalid_stops_diagnostics.md`.

The actual processes are IC's `ubs_agent.py` and `app_ui.py`. This is not a
manager-node endpoint problem: editing `mt5_manager/node.py` cannot fix it.
Reloading Python/UI changes requires restarting the desktop agent once idle.

The two reported rows were repaired with `assert_writable` and a before-image
backup at `runtime/invalid_stops_run445_before_20260906_235029.json`.
Other historical runs were not bulk-reclassified. AXI and RoboForex were not
modified. The fix does not alter strategy SL/TP parameters or rerun MT5.

Follow-up score/weight audit (2026-09-07): the two rows retain score -75,
confirmed by recomputing their stored metrics. Current probabilistic selection
treats both former `no_trades` and current `rejected` as negative base outcomes,
so their selection signals are unchanged. The legacy additive audit utility
changes from -40 to -140; it is not the active selection signal. Technical and
broker-block states remain neutral. No scoring coefficients or data changed in
this review. Full findings and 65 passing focused tests are documented in the
IC context file referenced above.
