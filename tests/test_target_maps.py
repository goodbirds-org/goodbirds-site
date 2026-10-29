import contextlib
import csv
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
import yaml

from scripts.build_target_map_page import make_popup, unique_sightings


ROOT = Path(__file__).resolve().parents[1]


def target_config():
    workflow = yaml.safe_load((ROOT / ".github/workflows/build-target-species.yml").read_text(encoding="utf-8"))
    return next(t for t in workflow["jobs"]["build-targets"]["strategy"]["matrix"]["target"]
                if t["slug"] == "north-cambridge-weird-ducks")


class TargetMapTests(unittest.TestCase):
    def test_north_cambridge_geometry_species_and_schedule(self):
        target = target_config()
        city_workflow = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8"))
        city = next(c for c in city_workflow["jobs"]["build"]["strategy"]["matrix"]["city"]
                    if c["slug"] == "north-cambridge")
        for target_key, city_key in [("center_lat", "center_lat"), ("center_lng", "center_lon"),
                                     ("dist_km", "default_radius_km"), ("ring_kms", "ring_kms")]:
            self.assertEqual(target[target_key], city[city_key])
        species = json.loads(target["species_json"])
        self.assertEqual(len(species), 20)
        taxonomy = {r["SPECIES_CODE"]: r["PRIMARY_COM_NAME"] for r in csv.DictReader(
            io.StringIO((ROOT / "scripts/data/eBird_taxonomy_v2024.csv").read_text(encoding="utf-8-sig")))}
        for sp in species:
            self.assertEqual(taxonomy[sp["code"]], sp["name"])
        self.assertEqual({sp["group"] for sp in species}, {"dabbler", "diver", "merganser", "other"})
        workflow = yaml.safe_load((ROOT / ".github/workflows/build-target-species.yml").read_text(encoding="utf-8"))
        # PyYAML's YAML 1.1 parser treats GitHub's `on` key as True.
        self.assertEqual(workflow[True]["schedule"], [{"cron": "30 9 * * *"}, {"cron": "30 18 * * *"}])

    def test_popup_retains_every_checklist_per_species_and_escapes_text(self):
        species = [{"code": "buffle", "name": "Bufflehead"}, {"code": "hoomer", "name": "Hooded Merganser"}]
        base = {"locId": "L1", "locName": "Pond <test>", "lat": 42.4, "lng": -71.13, "howMany": 2}
        rows = [dict(base, speciesCode="buffle", subId="S1", obsDt="2026-10-25 09:00"),
                dict(base, speciesCode="buffle", subId="S2", obsDt="2026-10-26 09:00"),
                dict(base, speciesCode="hoomer", subId="S1", obsDt="2026-10-25 09:00")]
        rows.append(dict(rows[0]))
        popup = make_popup(rows, species).html.render()
        self.assertEqual(popup.count("https://ebird.org/checklist/S1"), 2)
        self.assertEqual(popup.count("https://ebird.org/checklist/S2"), 1)
        self.assertIn("Pond &lt;test&gt;", popup)
        self.assertLess(popup.index("2026-10-26"), popup.index("2026-10-25"))
        self.assertEqual(len(unique_sightings(rows)), 3)
        self.assertEqual(len(unique_sightings(rows + [dict(rows[0], locId="L2")])), 4)
        self.assertIn("Checklist link unavailable", make_popup([dict(base, speciesCode="buffle")], species).html.render())

    def run_fetch(self, directory, response=None, side_effect=None):
        target = target_config()
        env = {"EBIRD_API_KEY": "test-key", "CENTER_LAT": target["center_lat"],
               "CENTER_LNG": target["center_lng"], "DIST_KM": target["dist_km"],
               "BACK_DAYS": target["back_days"], "RING_KMS": target["ring_kms"], "MAP_NOTE": target["note"],
               "OUTPUT_DIR": directory, "TARGET_SPECIES_JSON": target["species_json"]}
        with patch.dict(os.environ, env), patch("requests.get", return_value=response, side_effect=side_effect) as get:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                runpy.run_path(str(ROOT / "scripts/fetch_target_species.py"), run_name="__main__")
            return get

    def test_fetch_keeps_checklists_and_explicit_map_metadata(self):
        response = Mock()
        response.json.return_value = [{"subId": "S1", "locId": "L1"}, {"subId": "S2", "locId": "L1"}]
        with tempfile.TemporaryDirectory() as directory:
            get = self.run_fetch(directory, response)
            data = json.loads((Path(directory) / "observations.json").read_text())
        self.assertEqual(get.call_count, 20)
        self.assertEqual(len(data["observations"]), 40)
        self.assertEqual({o["subId"] for o in data["observations"]}, {"S1", "S2"})
        self.assertEqual(data["ringConfig"], {"unit": "km", "values": [1, 5, 10]})
        self.assertIn("Not all ducks", data["note"])
        self.assertNotIn("pending", data.values())
        self.assertEqual(get.call_args.kwargs["params"]["dist"], 10)
        self.assertEqual(get.call_args.kwargs["params"]["back"], 2)
        self.assertEqual(data["backDays"], 2)

    def test_failed_fetch_does_not_replace_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "observations.json"
            output.write_text('{"previous":true}')
            good = Mock()
            good.json.return_value = []
            with self.assertRaises(SystemExit) as error:
                self.run_fetch(directory, side_effect=[good] * 19 + [requests.Timeout("test timeout")])
            self.assertEqual(error.exception.code, 1)
            self.assertEqual(output.read_text(), '{"previous":true}')


if __name__ == "__main__":
    unittest.main()
