import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('validator', ROOT / 'scripts/validate_data.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def fixture():
    return {'meta': {'fighter_count': 2, 'matchup_count': 1, 'bout_count': 1, 'latest_resolved_event_date': '2026-05-16'},
            'nodes': [{'id': x, 'fightCount': 1, 'uniqueOpponentCount': 1} for x in ['a', 'b']],
            'links': [{'source': 'a', 'target': 'b', 'value': 1, 'bouts': [{}]}]}


class DataTests(unittest.TestCase):
    def test_committed_archive(self):
        for filename in validator.FILES:
            with self.subTest(filename=filename):
                validator.validate_payload(json.loads((ROOT / 'docs/data' / filename).read_text()))

    def test_valid_graph(self):
        self.assertEqual(validator.validate_payload(fixture()), 1)

    def test_empty_division(self):
        data = fixture(); data['nodes'] = []
        with self.assertRaisesRegex(ValueError, 'Empty division'): validator.validate_payload(data)

    def test_missing_endpoint(self):
        data = fixture(); data['links'][0]['target'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'endpoint'): validator.validate_payload(data)

    def test_duplicate_fighter(self):
        data = fixture(); data['nodes'].append(copy.deepcopy(data['nodes'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate fighter'): validator.validate_payload(data)

    def test_duplicate_matchup(self):
        data = fixture(); data['links'].append(copy.deepcopy(data['links'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate matchup'): validator.validate_payload(data)

    def test_inconsistent_totals(self):
        data = fixture(); data['nodes'][0]['fightCount'] = 99
        with self.assertRaisesRegex(ValueError, 'Fighter totals'): validator.validate_payload(data)

    def test_partial_refresh(self):
        previous = fixture(); previous['meta']['bout_count'] = 100
        with self.assertRaisesRegex(ValueError, 'disappeared'): validator.validate_payload(fixture(), previous)

    def test_coverage_regression(self):
        data = fixture(); data['meta']['latest_resolved_event_date'] = '2026-04-01'
        with self.assertRaisesRegex(ValueError, 'backwards'): validator.validate_payload(data, fixture())

    def test_metadata_counts(self):
        data = fixture(); data['meta']['bout_count'] = 3
        with self.assertRaisesRegex(ValueError, 'Metadata totals'): validator.validate_payload(data)


if __name__ == '__main__': unittest.main()
