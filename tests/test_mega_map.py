import contextlib
import csv
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_mega_map import build_map_html, load_allowed_species_codes, main


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "scripts/data"


class MegaTaxonomyTests(unittest.TestCase):
    def test_current_inputs_resolve_species_without_subspecies_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            allowed, counts = load_allowed_species_codes(DATA / "ABA_Checklist.csv", DATA / "eBird_taxonomy_v2026.csv", out, {4, 5})
            self.assertEqual(counts, {4: 124, 5: 150})
            code4 = set(json.loads((out / "aba4.json").read_text()))
            code5 = set(json.loads((out / "aba5.json").read_text()))
            self.assertFalse(code4 & code5)
            self.assertEqual(allowed, code4 | code5)
            self.assertTrue({"whimbr5", "sibsto1", "whcalb1"} <= code4)
            self.assertTrue({"whiter5", "wavalb", "hoopoe"} <= code5)
            self.assertNotIn("whcalb3", allowed)  # ISSF must not replace White-capped Albatross.
            self.assertNotIn("wanalb1", allowed)  # Snowy is not the broad ABA Wandering identification.
            with (out / "unresolved_names.csv").open(encoding="utf-8") as f:
                unresolved = list(csv.DictReader(f))
            self.assertEqual([r["UNRESOLVED_COMMON_NAME"] for r in unresolved], ["Wandering Albatross (sp.)"])
            with (out / "species_matches.csv").open(encoding="utf-8") as f:
                matches = list(csv.DictReader(f))
            self.assertEqual(len(matches), 274)
            tax = {r["SPECIES_CODE"]: r for r in csv.DictReader((DATA / "eBird_taxonomy_v2026.csv").read_text(encoding="utf-8-sig").splitlines())}
            self.assertTrue(all(tax[r["SPECIES_CODE"]]["CATEGORY"] == "species" for r in matches))

    def test_legacy_columns_and_empty_diagnostics_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            aba = out / "aba.csv"
            tax = out / "taxonomy.csv"
            aba.write_text("Common Name,Scientific Name,ABA Checklist Code\nTest Bird,Testus birdus,5\n", encoding="utf-8")
            tax.write_text("PRIMARY_COM_NAME,SPECIES_CODE,CATEGORY\nTest Bird,testbi,species\nTest Bird (island),testbi2,issf\n", encoding="utf-8-sig")
            (out / "unresolved_names.csv").write_text("UNRESOLVED_COMMON_NAME\nStale name\n")
            allowed, _ = load_allowed_species_codes(aba, tax, out, {5})
            self.assertEqual(allowed, {"testbi"})
            self.assertNotIn("Stale name", (out / "unresolved_names.csv").read_text())

    def test_ambiguous_match_is_reported_instead_of_chosen(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            aba = out / "aba.csv"
            tax = out / "taxonomy.csv"
            aba.write_text("Common Name,Scientific Name,ABA Checklist Code\nTest Bird,,5\n")
            tax.write_text("COMMON_NAME,SPECIES_CODE,CATEGORY\nTest Bird,test1,species\nTest Bird,test2,species\n")
            allowed, _ = load_allowed_species_codes(aba, tax, out, {5})
            self.assertFalse(allowed)
            self.assertIn("ambiguous species match", (out / "unresolved_names.csv").read_text())

    def test_empty_map_still_produces_page(self):
        with tempfile.TemporaryDirectory() as directory:
            out = build_map_html(Path(directory), [], set(), set(), 2, "Goodbirds Mega Map")
            self.assertIn("Goodbirds Mega Map", out.read_text(encoding="utf-8"))

    def test_build_filters_records_and_records_verified_versions(self):
        rows = [
            {"speciesCode": "whiter5", "comName": "Little White-Tern", "lat": 22, "lng": -158, "subId": "S1"},
            {"speciesCode": "wavalb", "comName": "Waved Albatross", "lat": 32, "lng": -117, "subId": "S2"},
            {"speciesCode": "mallar3", "comName": "Mallard", "lat": 42, "lng": -71, "subId": "S3"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            argv = ["build_mega_map.py", "--aba_csv", str(DATA / "ABA_Checklist.csv"), "--taxonomy_csv", str(DATA / "eBird_taxonomy_v2026.csv"), "--out_dir", directory, "--codes", "4,5"]
            with patch.object(sys, "argv", argv), patch.dict(os.environ, {"MEGA_MODE": "aba4_5_only", "MEGA_BACK_DAYS_RECENT": "2", "MEGA_NATIONAL_MAX": "60", "MEGA_PER_SPECIES_MAX": "0"}), patch("scripts.build_mega_map.fetch_recent_notables_sharded", return_value=rows), contextlib.redirect_stdout(io.StringIO()):
                main()
            summary = json.loads((Path(directory) / "summary.json").read_text())
            self.assertEqual(summary["count_megas"], 2)
            self.assertEqual(summary["taxonomy_version"], "2026")
            self.assertEqual(summary["aba_checklist_version"], "8.19")
            page = (Path(directory) / "index.html").read_text(encoding="utf-8")
            self.assertIn("Little White-Tern", page)
            self.assertNotIn("Mallard", page)


if __name__ == "__main__":
    unittest.main()
