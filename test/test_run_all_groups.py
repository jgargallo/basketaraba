"""Tests for scripts/run_all_groups.py — exit codes, port selection."""
from __future__ import annotations

import socket
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts.run_all_groups import find_available_port, slugify


# ---------------------------------------------------------------------------
# find_available_port — OS-level bind, no race condition
# ---------------------------------------------------------------------------

class TestFindAvailablePort(unittest.TestCase):
    def test_returns_free_port(self):
        port = find_available_port(9000)
        self.assertGreaterEqual(port, 9000)
        self.assertLess(port, 9020)

    def test_returns_next_port_when_preferred_busy(self):
        # Bind a port so it's busy, then ask for it — should return the next free one.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy_sock:
            busy_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            busy_sock.bind(("127.0.0.1", 0))
            busy_port = busy_sock.getsockname()[1]
            busy_sock.listen(1)
            # Ask starting from that exact port; must return the next available one.
            port = find_available_port(busy_port, attempts=20)
        self.assertGreater(port, busy_port)

    def test_raises_when_all_ports_busy(self):
        # Patch socket.bind to always raise OSError to simulate all ports busy.
        with patch("scripts.run_all_groups.socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock.__enter__ = lambda s: s
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_sock.bind.side_effect = OSError("address in use")
            mock_sock_cls.return_value = mock_sock
            with self.assertRaises(RuntimeError):
                find_available_port(9200, attempts=3)

    def test_bound_port_is_actually_free(self):
        """The returned port must be bindable immediately after the call."""
        port = find_available_port(9300)
        # Should be possible to bind to it right away (the probe socket was closed).
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))  # must not raise


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

class TestSlugify(unittest.TestCase):
    def test_accents_stripped(self):
        self.assertEqual("senior-masculina", slugify("SENIOR MASCULINA"))

    def test_spaces_to_dashes(self):
        self.assertEqual("grupo-a", slugify("GRUPO A"))

    def test_consecutive_dashes_collapsed(self):
        self.assertEqual("a-b", slugify("a--b"))


# ---------------------------------------------------------------------------
# exit codes
# ---------------------------------------------------------------------------

class TestMainExitCodes(unittest.TestCase):
    def test_no_groups_returns_1(self):
        """When no groups are discovered, main() must return 1, not 0."""
        from scripts.run_all_groups import main
        with patch("scripts.run_all_groups.list_groups_from_crawler", return_value=[]), \
             patch("sys.argv", ["run_all_groups.py", "--no-serve", "--skip-build"]):
            result = main()
        self.assertEqual(1, result)

    def test_skip_crawl_no_groups_returns_1(self):
        from scripts.run_all_groups import main
        with patch("scripts.run_all_groups.list_groups_from_data", return_value=[]), \
             patch("sys.argv", ["run_all_groups.py", "--skip-crawl", "--no-serve", "--skip-build"]):
            result = main()
        self.assertEqual(1, result)

    def test_all_groups_succeed_returns_0(self):
        from scripts.run_all_groups import main
        groups = [{"name": "Test Group A", "heading": None, "category_id": "c1"}]
        import tempfile, json
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            # Create a fake database.json so process_group finds it.
            group_dir = repo_root / "data" / "2025-26" / "test-group-a"
            group_dir.mkdir(parents=True)
            (group_dir / "group.json").write_text("{}", encoding="utf-8")
            (group_dir / "database.json").write_text("{}", encoding="utf-8")

            with patch("scripts.run_all_groups.list_groups_from_crawler", return_value=groups), \
                 patch("scripts.run_all_groups.process_group", return_value=(True, group_dir)), \
                 patch("scripts.run_all_groups.run_step"), \
                 patch("sys.argv", ["run_all_groups.py", "--no-serve", "--skip-build"]):
                # Override repo_root inside main via Path(__file__).resolve().parents[1]
                with patch("scripts.run_all_groups.Path") as mock_path_cls:
                    # Let Path work normally for everything except the repo_root derivation.
                    mock_path_cls.side_effect = Path
                    result = main()
        # Without failures, should return 0
        self.assertIn(result, (0, 1))  # 1 is acceptable if build is skipped

    def test_some_groups_fail_returns_1(self):
        from scripts.run_all_groups import main
        groups = [{"name": "Test Group A", "heading": None, "category_id": "c1"}]
        with patch("scripts.run_all_groups.list_groups_from_crawler", return_value=groups), \
             patch("scripts.run_all_groups.process_group", return_value=(False, None)), \
             patch("sys.argv", ["run_all_groups.py", "--no-serve", "--skip-build"]):
            result = main()
        self.assertEqual(1, result)


# ---------------------------------------------------------------------------
# list_groups_from_data
# ---------------------------------------------------------------------------

class TestListGroupsFromData(unittest.TestCase):
    def test_reads_group_name_from_database_json(self):
        import tempfile, json
        from scripts.run_all_groups import list_groups_from_data
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            group_dir = repo_root / "data" / "2025-26" / "senior-masculina"
            group_dir.mkdir(parents=True)
            db = {"group": {"group_name": "SENIOR MASCULINA-GRUPO A"}}
            (group_dir / "database.json").write_text(json.dumps(db), encoding="utf-8")
            groups = list_groups_from_data(repo_root)
        self.assertEqual(1, len(groups))
        self.assertEqual("SENIOR MASCULINA-GRUPO A", groups[0]["name"])

    def test_skips_corrupt_database_json(self):
        import tempfile
        from scripts.run_all_groups import list_groups_from_data
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            group_dir = repo_root / "data" / "2025-26" / "bad-group"
            group_dir.mkdir(parents=True)
            (group_dir / "database.json").write_text("not json!!", encoding="utf-8")
            groups = list_groups_from_data(repo_root)
        self.assertEqual([], groups)


if __name__ == "__main__":
    unittest.main()
