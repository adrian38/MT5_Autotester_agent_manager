from __future__ import annotations

import unittest

from mt5_manager.docker_entrypoint import docker_config


class DockerEntrypointTests(unittest.TestCase):
    def test_docker_config_reuses_tokens_and_maps_host_paths(self) -> None:
        source = {
            "host": "127.0.0.1",
            "nodes": [
                {
                    "id": "ic",
                    "url": "http://127.0.0.1:8761",
                    "token": "secret",
                    "portfolio_project_dir": r"C:\projects\ic",
                    "portfolio_broker": "ICTRADING",
                    "portfolio_memory_path": r"C:\projects\ic\outputs\memory.sqlite",
                    "portfolio_memory_paths": [
                        {"account_type": "STANDARD", "path": r"C:\projects\ic\outputs\other.sqlite"}
                    ],
                },
                {
                    "id": "robo",
                    "url": "http://192.168.1.152:8761",
                    "token": "remote-secret",
                    "portfolio_project_dir": r"X:\TRADING\robo",
                    "portfolio_broker": "ROBOFOREX",
                },
            ],
        }

        result = docker_config(source)

        self.assertEqual(result["host"], "0.0.0.0")
        self.assertEqual(result["export_mode"], "download")
        self.assertEqual(result["nodes"][0]["url"], "http://host.docker.internal:8761")
        self.assertEqual(result["nodes"][0]["token"], "secret")
        self.assertEqual(result["nodes"][0]["portfolio_project_dir"], "/data/ic")
        self.assertEqual(result["nodes"][0]["portfolio_memory_path"], "/data/ic/outputs/memory.sqlite")
        self.assertEqual(
            result["nodes"][0]["portfolio_memory_paths"][0]["path"],
            "/data/ic/outputs/other.sqlite",
        )
        self.assertEqual(result["nodes"][1]["url"], "http://192.168.1.152:8761")
        self.assertEqual(
            result["nodes"][1]["portfolio_project_dir"],
            "/data/roboforex/TRADING/MT5_Autotester_agent",
        )
        self.assertEqual(source["host"], "127.0.0.1", "La configuración original no debe mutar")


if __name__ == "__main__":
    unittest.main()
