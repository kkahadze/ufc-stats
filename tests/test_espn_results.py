import copy
from datetime import date
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import espn_results as espn
from validate_data import validate_payload


def fighter(id, label):
    return {'id': id, 'label': label, 'group': 'Lightweight', 'fightCount': 1, 'uniqueOpponentCount': 1,
            'isFormerChampion': True, 'countryCode': 'US'}


def archive():
    return {'Lightweight': {
        'meta': {'latest_resolved_event_date': '2026-05-16', 'fighter_count': 2, 'matchup_count': 1, 'bout_count': 1},
        'nodes': [fighter('a', 'Fighter A'), fighter('b', 'Fighter B')],
        'links': [{'source': 'a', 'target': 'b', 'value': 1, 'bouts': [
            {'eventName': 'Old UFC event', 'eventDate': '2026-05-16', 'isTitleBout': True}]}],
    }}


def event():
    return {'id': '100', 'name': 'UFC Fight Night: Test', 'date': '2026-05-30T21:00Z',
            'status': {'type': {'completed': True}}, 'competitions': [{
                'id': '200', 'type': {'abbreviation': 'Lightweight'},
                'status': {'type': {'completed': True}},
                'competitors': [
                    {'id': '1', 'winner': True, 'athlete': {'displayName': 'Fighter A'}},
                    {'id': '2', 'winner': False, 'athlete': {'displayName': 'Fighter B'}},
                ],
            }]}


def status():
    return {'type': {'completed': True}, 'period': 2, 'displayClock': '1:45',
            'result': {'name': 'kotko', 'displayName': 'KO/TKO', 'displayDescription': 'Punches'}}


class ESPNResultsTests(unittest.TestCase):
    def refresh(self, payload=None, card=None, result=None):
        feed = {'events': [card or event()]}
        with patch.object(espn, 'load_status', return_value=result or status()):
            return espn.refresh_archive(payload or archive(), lambda _: json.dumps(feed), date(2026, 5, 31))

    def test_preserves_history_ids_and_metadata(self):
        original = archive()
        updated = self.refresh(original)['Lightweight']
        self.assertEqual(updated['links'][0]['bouts'][0], original['Lightweight']['links'][0]['bouts'][0])
        self.assertEqual([n['id'] for n in updated['nodes']], ['a', 'b'])
        self.assertTrue(updated['nodes'][0]['isFormerChampion'])
        self.assertEqual(updated['meta']['bout_count'], 2)
        self.assertIsNone(updated['links'][0]['bouts'][1]['isTitleBout'])
        self.assertEqual(original['Lightweight']['meta']['bout_count'], 1)
        validate_payload(updated, original['Lightweight'])

    def test_refresh_is_idempotent(self):
        first = self.refresh()
        self.assertEqual(first, self.refresh(first))

    def test_corrections_replace_instead_of_duplicate(self):
        first = self.refresh()
        corrected = status()
        corrected['displayClock'] = '2:00'
        second = self.refresh(first, result=corrected)['Lightweight']
        self.assertEqual(second['meta']['bout_count'], 2)
        self.assertEqual(second['links'][0]['bouts'][1]['time'], '2:00')

    def test_missing_published_bout_fails_closed(self):
        first = self.refresh()
        changed = event()
        changed['competitions'][0]['id'] = 'different'
        with self.assertRaisesRegex(ValueError, 'disappeared'):
            self.refresh(first, changed)

    def test_excludes_contender_series_and_upcoming_cards(self):
        dwcs, future, unfinished = copy.deepcopy(event()), copy.deepcopy(event()), copy.deepcopy(event())
        dwcs['name'] = "Dana White's Contender Series"
        future['date'] = '2026-06-06T21:00Z'
        unfinished['status']['type']['completed'] = False
        self.assertEqual(espn.completed_ufc_events({'events': [dwcs, future, unfinished]}, date(2026, 5, 31)), [])

    def test_utc_midnight_uses_event_calendar_date(self):
        card = event()
        card['date'] = '2026-06-15T00:00Z'
        self.assertEqual(espn.event_date(card), date(2026, 6, 14))

    def test_invalid_feed_fails(self):
        with self.assertRaises(ValueError):
            espn.completed_ufc_events({}, date(2026, 5, 31))
        self.assertEqual(espn.completed_ufc_events({'events': []}, date(2026, 5, 31)), [])

    def test_ambiguous_identity_fails(self):
        nodes = {'Lightweight': {'a': fighter('a', 'Bruno Silva'), 'b': fighter('b', 'Bruno Silva')}}
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            espn.resolve_fighter({'id': '1', 'athlete': {'displayName': 'Bruno Silva'}}, 'Lightweight', nodes)

    def test_alias_and_transliteration_preserve_ids(self):
        nodes = {'Lightweight': {'a': fighter('a', 'Benardo Sopaj'), 'b': fighter('b', 'Klaudia Sygula')}}
        for id, label, expected in [('5080883', 'Bernardo Sopaj', 'a'), ('2', 'Klaudia Syguła', 'b')]:
            resolved, _ = espn.resolve_fighter({'id': id, 'athlete': {'displayName': label}}, 'Lightweight', nodes)
            self.assertEqual(resolved, expected)

    def test_new_fighter_uses_stable_source_id(self):
        card = event()
        card['competitions'][0]['competitors'][1]['athlete']['displayName'] = 'New Fighter'
        updated = self.refresh(card=card)
        self.assertIn('espn:2', [n['id'] for n in updated['Lightweight']['nodes']])
        self.assertEqual(updated, self.refresh(updated, card))

    def test_missing_winner_is_not_invented(self):
        card = event()
        card['competitions'][0]['competitors'][0]['winner'] = False
        with self.assertRaisesRegex(ValueError, 'Ambiguous completed result'):
            self.refresh(card=card)
        drawn = status()
        drawn['result'] = {'name': 'draw', 'displayName': 'Draw'}
        updated = self.refresh(card=card, result=drawn)
        self.assertEqual(updated['Lightweight']['links'][0]['bouts'][1]['resultSummary'], 'Draw')

    def test_incomplete_status_is_never_cached(self):
        with patch.object(espn, 'CACHE', Path('/tmp/ufc-test-no-status-cache')):
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                espn.load_status('1', '2', date(2026, 5, 30), lambda _: '{}', date(2026, 5, 31))


if __name__ == '__main__':
    unittest.main()
