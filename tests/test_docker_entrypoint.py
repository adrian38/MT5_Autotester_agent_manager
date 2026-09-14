from __future__ import annotations

import unittest
from unittest import mock

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
                    "portfolio_project_dir": r"G:\TRADING\MT5_Autotester_agent",
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
        self.assertEqual(result["nodes"][0]["node_project_dir"], r"C:\projects\ic")
        self.assertEqual(result["nodes"][0]["portfolio_memory_path"], "/data/ic/outputs/memory.sqlite")
        self.assertEqual(
            result["nodes"][0]["portfolio_memory_paths"][0]["path"],
            "/data/ic/outputs/other.sqlite",
        )
        self.assertEqual(result["nodes"][1]["url"], "http://192.168.1.152:8761")
        self.assertEqual(result["nodes"][1]["portfolio_project_dir"], "/data/roboforex")
        self.assertEqual(source["host"], "127.0.0.1", "La configuración original no debe mutar")

    def test_proxy_broker_does_not_require_a_container_project_mount(self) -> None:
        source = {
            "host": "127.0.0.1",
            "nodes": [{
                "id": "ic",
                "url": "http://192.168.1.146:8761",
                "token": "secret",
                "portfolio_project_dir": r"Z:\remote\ic",
                "portfolio_broker": "ICTRADING",
                "portfolio_memory_path": r"Z:\remote\ic\outputs\memory.sqlite",
                "portfolio_memory_paths": [
                    {"account_type": "STANDARD", "path": r"Z:\remote\ic\outputs\other.sqlite"}
                ],
            }],
        }

        with mock.patch.dict(
            "mt5_manager.docker_entrypoint.os.environ",
            {"MT5_MANAGER_PROXY_BROKERS": " ictrading "},
            clear=False,
        ):
            result = docker_config(source)

        node = result["nodes"][0]
        self.assertNotIn("portfolio_project_dir", node)
        self.assertNotIn("portfolio_memory_path", node)
        self.assertNotIn("portfolio_memory_paths", node)
        self.assertEqual(node["url"], "http://192.168.1.146:8761")
        self.assertIn("portfolio_project_dir", source["nodes"][0])


if __name__ == "__main__":
    unittest.main()
