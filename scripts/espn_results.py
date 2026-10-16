"""Supplement the preserved UFC Stats archive when its site is unavailable."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
import unicodedata
from zoneinfo import ZoneInfo

SCOREBOARD = 'https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard'
CORE = 'https://sports.core.api.espn.com/v2/sports/mma/leagues/ufc/events'
CACHE = Path(__file__).resolve().parents[1] / '.cache' / 'ufc-stats' / 'espn'
# Verified differences between ESPN display names and the historical UFC Stats IDs.
NAME_ALIASES = {
    '5080883': 'Benardo Sopaj',
    '5223435': 'Jose Delgado',
    '5212738': 'Michael Aswell Jr.',
}


def name_key(name):
    name = name.translate(str.maketrans({'ł': 'l', 'Ł': 'L'}))
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', ' ', name).strip()


def event_date(event):
    # ESPN's US event calendar uses Eastern time, including evening cards after UTC midnight.
    return datetime.fromisoformat(event['date'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/New_York')).date()


def completed_ufc_events(feed, today):
    events = feed.get('events')
    if not isinstance(events, list):
        raise ValueError('ESPN returned no event archive; refusing an invalid refresh')
    selected = []
    for event in events:
        if not re.match(r'^(UFC\b|Noche UFC\b)', event['name']):
            continue  # The same feed also contains Contender Series and other promotions.
        if event_date(event) > today or not event['status']['type'].get('completed'):
            continue
        if not event.get('competitions'):
            raise ValueError('Completed UFC event has no bouts')
        selected.append(event)
    return selected


def resolve_fighter(competitor, division, nodes):
    espn_id = str(competitor['id'])
    all_nodes = [node for payload in nodes.values() for node in payload.values()]
    known = {n['id'] for n in all_nodes if n.get('espnId') == espn_id}
    label = competitor['athlete']['displayName']
    key = name_key(NAME_ALIASES.get(espn_id, label))
    if not known:
        known = {n['id'] for n in all_nodes if name_key(n['label']) == key}
    if len(known) > 1:
        known = {n['id'] for n in nodes[division].values() if n['id'] in known}
        if len(known) != 1:
            raise ValueError(f'Ambiguous fighter identity: {label} ({espn_id})')
    fighter_id = next(iter(known)) if known else f'espn:{espn_id}'
    if fighter_id not in nodes[division]:
        existing = next((n for n in all_nodes if n['id'] == fighter_id), {})
        nodes[division][fighter_id] = {
            'id': fighter_id, 'label': existing.get('label', label), 'group': division,
            'fightCount': 0, 'uniqueOpponentCount': 0,
            'isCurrentChampion': False, 'isCurrentlyRanked': False, 'currentRank': None,
            'isFormerChampion': False, 'isFormerTitleChallenger': False,
            'hasTitleFightInDivision': False,
            **{k: existing.get(k) for k in ('countryCode', 'countryName', 'flagEmoji')},
        }
    node = nodes[division][fighter_id]
    if node.get('espnId') not in (None, espn_id):
        raise ValueError(f'Conflicting ESPN identities for {label}')
    node['espnId'] = espn_id
    return fighter_id, node['label']


def load_status(event_id, bout_id, bout_date, fetch_text, today):
    path = CACHE / f'{event_id}-{bout_id}.json'
    if path.exists() and bout_date < today - timedelta(days=30):
        status = json.loads(path.read_text())
    else:
        status = json.loads(fetch_text(f'{CORE}/{event_id}/competitions/{bout_id}/status?lang=en&region=us'))
    if not status.get('type', {}).get('completed') or not status.get('result', {}).get('displayName'):
        raise ValueError(f'Incomplete ESPN result: {event_id}/{bout_id}')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status))
    return status


def refresh_archive(payloads, fetch_text, today, country_lookup=lambda _: None):
    result = deepcopy(payloads)
    cutoffs = {p['meta'].get('ufcstats_coverage_through', p['meta']['latest_resolved_event_date']) for p in result.values()}
    if len(cutoffs) != 1:
        raise ValueError('Division archive coverage dates disagree')
    cutoff = date.fromisoformat(cutoffs.pop())
    # Always re-read the supplemental period so missed cards and corrections are recovered.
    # Limit each request to a calendar month to avoid silent API pagination/truncation.
    events = {}
    start = cutoff
    while start <= today:
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = min(next_month - timedelta(days=1), today)
        feed = json.loads(fetch_text(f'{SCOREBOARD}?dates={start:%Y%m%d}-{end:%Y%m%d}&limit=1000'))
        for event in completed_ufc_events(feed, today):
            if event_date(event) > cutoff:
                events[event['id']] = event
        start = next_month
    if not events and today > cutoff + timedelta(days=30):
        raise ValueError('No recent UFC results found; refusing to mark an old archive fresh')

    nodes = {d: {n['id']: n for n in p['nodes']} for d, p in result.items()}
    edges = {}
    previous_ids = set()
    for division, payload in result.items():
        edges[division] = defaultdict(list)
        for link in payload['links']:
            for bout in link['bouts']:
                if bout.get('source') == 'ESPN':
                    previous_ids.add(bout['sourceBoutId'])
                else:
                    edges[division][tuple(sorted((link['source'], link['target'])))].append(bout)

    jobs = []
    seen = set()
    for event in sorted(events.values(), key=lambda e: (event_date(e), e['id'])):
        for bout in event['competitions']:
            division = bout['type']['abbreviation'].replace('W ', "Women's ", 1)
            if division == 'Catch Weight':
                continue  # Same division coverage as the primary UFC Stats parser.
            if division not in result:
                raise ValueError(f'Unexpected ESPN division: {division}')
            if not bout['status']['type'].get('completed'):
                continue  # Canceled or unresolved bouts are not results.
            if bout['id'] in seen or len(bout['competitors']) != 2:
                raise ValueError('Duplicate bout ID or invalid competitors')
            seen.add(bout['id'])
            jobs.append((event, bout, division))
    if not previous_ids <= seen:
        raise ValueError('Previously published ESPN bouts disappeared; manual review required')

    def status_for(job):
        event, bout, _ = job
        return load_status(event['id'], bout['id'], event_date(event), fetch_text, today)

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(status_for, jobs))
    for (event, bout, division), status in zip(jobs, statuses):
        fighters = [resolve_fighter(c, division, nodes) for c in bout['competitors']]
        if fighters[0][0] == fighters[1][0]:
            raise ValueError('Both competitors resolved to one fighter')
        for competitor, (fighter_id, _) in zip(bout['competitors'], fighters):
            node = nodes[division][fighter_id]
            if not node.get('countryCode'):
                country = country_lookup(competitor.get('athlete', {}).get('flag', {}).get('alt'))
                if country:
                    node.update(country)
        winners = [i for i, c in enumerate(bout['competitors']) if c.get('winner')]
        finish = status['result']['displayName']
        if len(winners) == 1:
            i = winners[0]
            summary = f'{fighters[i][1]} def. {fighters[1-i][1]}'
        elif not winners and status['result']['name'].lower() in ('draw', 'majoritydraw', 'splitdraw', 'unanimousdraw', 'nc', 'nocontest', 'no-contest'):
            summary = 'Draw' if 'draw' in status['result']['name'].lower() else 'No contest'
        else:
            raise ValueError(f'Ambiguous completed result: {bout["id"]} ({status["result"]})')
        detail = status['result'].get('displayDescription')
        if detail and detail != finish:
            finish += ' · ' + detail
        pair = tuple(sorted(f[0] for f in fighters))
        edges[division][pair].append({
            'eventName': event['name'], 'eventDate': event_date(event).isoformat(),
            'resultSummary': summary, 'method': finish,
            'round': str(status['period']), 'time': status['displayClock'],
            'isTitleBout': None,  # ESPN results do not reliably distinguish championship bouts.
            'source': 'ESPN', 'sourceEventId': event['id'], 'sourceBoutId': bout['id'],
            'sourceUrl': f'https://www.espn.com/mma/fightcenter/_/id/{event["id"]}/league/ufc/fightId/{bout["id"]}',
        })
    latest = max([cutoff] + [event_date(e) for e in events.values()])
    for division, payload in result.items():
        counts, opponents = Counter(), defaultdict(set)
        links = []
        for (a, b), bouts in sorted(edges[division].items(), key=lambda item: tuple(nodes[division][i]['label'].casefold() for i in item[0])):
            if not bouts:
                continue
            counts.update({a: len(bouts), b: len(bouts)})
            opponents[a].add(b)
            opponents[b].add(a)
            links.append({'source': a, 'target': b, 'value': len(bouts), 'bouts': bouts})
        payload['nodes'] = sorted((n for i, n in nodes[division].items() if counts[i]), key=lambda n: n['label'].casefold())
        for node in payload['nodes']:
            node['fightCount'] = counts[node['id']]
            node['uniqueOpponentCount'] = len(opponents[node['id']])
        payload['links'] = links
        payload['meta'].update({
            'source_name': 'UFC Stats archive + ESPN results',
            'supplement_source_url': SCOREBOARD,
            'ufcstats_coverage_through': cutoff.isoformat(),
            'latest_resolved_event_date': latest.isoformat(),
            'results_checked_on': today.isoformat(),
            'title_metadata_through': payload['meta'].get('title_metadata_through', cutoff.isoformat()),
            'fighter_count': len(payload['nodes']), 'matchup_count': len(links),
            'bout_count': sum(l['value'] for l in links),
        })
    print(f'ESPN supplement: {len(jobs)} bouts across {len(events)} completed UFC events; through {latest}.')
    return result
