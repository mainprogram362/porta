"""Ownership and explicit snapshot replacement, without touching live documents."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import patch
from uuid import uuid4
from PySide6.QtWidgets import QApplication, QMessageBox
from foundation.record_bundle import RecordBundle, RecordBundleRow
from foundation.record_bundle_fields import add_extensionless_field
from apps.launcher.record_center import RecordCenter
from apps.file_tools.file_manager.window import FileManagerScreen


class RecordOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_edits_are_isolated_and_update_resets_links_only_after_success(self):
        with patch("apps.launcher.record_center.server_name", return_value="prt-test-" + uuid4().hex[:12]):
            center = RecordCenter()
        manager = FileManagerScreen(lambda: None)
        editors = []
        try:
            bundle = RecordBundle("", ("名前", "値"), (RecordBundleRow("row", ("movie.mp4", "x")),))
            first = center.create_record(bundle)
            second = center.create_record(bundle)
            editors = [entry["window"] for entry in center.entries.values()]
            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual(len(center.handle({"action": "list"})["records"]), 2)
            with patch("foundation.record_service.request", side_effect=center.handle):
                self.assertTrue(manager.acquire_record(first["id"]))
                manager.search_results_input.setPlainText("/tmp/movie.mp4")
                manager._select_record_link_field(0)
                original_links = manager._record_linkage
                editor = center.entries[first["id"]]["window"]
                editor._replace_bundle_from_field_transform(add_extensionless_field(bundle, 0, "拡張子なし"))
                self.assertEqual(manager._record_link_bundle, bundle)
                self.assertIs(manager._record_linkage, original_links)
                self.assertTrue(manager.acquire_record(first["id"]))
                self.assertEqual(len(manager._record_link_bundle.field_names), 3)
                self.assertIsNone(manager._record_link_field_index)
                self.assertIsNone(manager._record_linkage)
                self.assertEqual(manager._record_source_revision, 2)
                manager._select_record_link_field(0)
                snapshot = manager._record_link_bundle
                links = manager._record_linkage
                center.retire(first["id"])
                with patch.object(QMessageBox, "warning"):
                    self.assertFalse(manager.acquire_record(first["id"]))
                self.assertIs(manager._record_link_bundle, snapshot)
                self.assertIs(manager._record_linkage, links)
                self.assertTrue(center.handle({"action": "get", "id": second["id"]})["ok"])
        finally:
            manager.close()
            for editor in editors:
                editor.hide()
                editor.deleteLater()
            center.entries.clear()
            center.server.close()
            center.lock.unlock()
            center.hide()
            center.deleteLater()


if __name__ == "__main__":
    unittest.main()
