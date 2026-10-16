# UFC Fight Map
A force-directed visualization of UFC fight history by division. The site can be viewed [here](https://kkahadze.github.io/ufc-stats).

## Explore the map

Choose a division, roster, and minimum fight count, then search the visible fighters to highlight their opponents. Fighters can also be selected with Enter or Space when focused. Drag fighters to rearrange the network or drag the background to pan. Use the zoom buttons, Ctrl/Command-scroll, or the **Fit view** button to adjust the map.

On phones and tablets, **Filters** reveals the roster and minimum-fight controls. Tap fighters or matchup lines for persistent details below the map. Swipe normally to scroll the page; enable **Move map** to drag fighters, pan, or pinch the network, then **Done moving** to resume scrolling.

The date above the map shows the fight-results snapshot; rankings are saved metadata, not live standings. Failed division downloads show a retry button, and switching divisions keeps the latest selection active even if earlier requests finish later.

## Data source

The primary history source is UFC Stats. When it is unavailable, ESPN’s public UFC results feed supplements the preserved archive:

- completed events index: `http://ufcstats.com/statistics/events/completed?page=all`
- event result pages: `http://ufcstats.com/event-details/...`
- current champion and ranking snapshot: `https://www.ufc.com/rankings`
- fighter flag metadata: recent official UFC event-card pages from `https://www.ufc.com/events`, using each fighter's most recent UFC card appearance as the primary flag source
- fallback fighter country metadata: `https://www.ufc.com/athletes/all` plus cached UFC athlete profile pages when an event-card flag cannot be resolved

The generated graph files cover the current men's and women's UFC divisions. Existing UFC Stats fighter IDs are preserved. ESPN-only newcomers use namespaced ESPN athlete IDs; verified name aliases and stored ESPN IDs keep subsequent updates attached to the same fighters. Unresolved future bouts are skipped. Each node also includes:

- fight count in that division
- unique opponent count
- current champion flag
- current UFC Top 15 flag when that division has an active rankings table
- country code, country name, and flag emoji for the fighter when an official UFC event card or fallback athlete data can resolve it

## Refresh the data

```bash
python3 -m pip install -r requirements.txt
python3 scripts/update_fight_data.py
```

The scraper validates all 12 division payloads before writing `docs/data/`.

When UFC Stats is blocked, the updater reads ESPN’s monthly UFC scoreboards and detailed completed-bout results. It preserves the original archive and rebuilds the supplemental period on every run, allowing missed cards and recent corrections to be recovered without duplicate bouts. Older result details are cached; the most recent 30 days are fetched again. Contender Series, future/unresolved bouts, and catchweight fights are excluded. ESPN event dates use the US Eastern event calendar. Missing previously published results or ambiguous identities fail the refresh instead of publishing partial data.

ESPN results do **not** refresh UFC rankings or title classifications. Those remain the saved UFC snapshot, with the title-history cutoff shown separately on the site. New bouts have an unknown title-bout flag rather than an invented classification. Each supplemental bout records its ESPN source URL and IDs.

### Daily publication

`Update Fight Data` runs every day at **09:17 UTC** and supports **Run workflow** in GitHub Actions. It installs dependencies, refreshes the archive, runs the regression suite and validation, then commits changes to `master`. The successful-check date is saved daily so visitors can distinguish the last completed event from the last refresh attempt.

The job calls `Deploy Site` explicitly after a successful refresh. This is necessary because pushes made with `GITHUB_TOKEN` do not trigger ordinary Pages builds. `Deploy Site` also runs for normal pushes to `master`; Pages must use **GitHub Actions** as its publishing source. No paid service, API key, or personal access token is needed. GitHub may delay scheduled jobs; this is a daily refresh, not a live feed.

If both sources fail or validation fails, the job fails and leaves the published site intact. Inspect the `Update Fight Data` run logs to diagnose a failure, then rerun the workflow. The site’s last-check date advances only after a successful refresh.

It also keeps a local cache in `.cache/ufc-stats/` so repeated runs do not re-download the full event archive. That cache now includes:

- UFC Stats event pages
- the UFC rankings snapshot
- the UFC.com events archive pages
- cached UFC event-card pages used for fighter flag resolution
- the UFC athlete directory index
- the UFC athlete country/flag lookup
- per-athlete profile pages only for fighters the directory filters cannot fully resolve

The first run after introducing the flag metadata is slower because it has to fill those caches. After that, the normal refresh path reuses them. By default the scraper still refreshes the newest UFC Stats result pages, the newest UFC event-archive pages, and the newest cached UFC event cards to pick up new results and current walkout-flag changes without recrawling the entire UFC.com archive. To ignore the cache:

```bash
python3 scripts/update_fight_data.py --refresh-all
```

When the primary UFC Stats path is available but UFC.com rejects requests from an automated runner, the normal refresh path reuses the already-committed ranking and country metadata from `docs/data/` rather than failing the whole data refresh. `--refresh-all` still requires live source fetches and will fail instead of using committed fallback metadata.

## Quality checks

These checks use only Node.js 22+ and Python 3.9+ standard libraries:

```bash
node --check docs/main.js
node --test tests/frontend.test.cjs
python3 -m unittest discover -s tests -v
python3 scripts/validate_data.py
```

`Site QA` runs on pushes and pull requests. The refresh workflow additionally runs
`python3 scripts/validate_data.py --compare-ref HEAD` before committing data. It rejects
empty divisions, invalid graph references/counts, backwards coverage dates, and a
loss of more than 10% of any division's historical bouts. Deliberate large corrections
require review rather than automatic publication. These are structural safeguards;
they do not establish the historical accuracy of ranking or title classifications.

For UI changes, also check desktop and mobile views, keyboard fighter selection,
search/clear, dragging, zoom/fit, empty filters, and division switching in a browser.
Dataset requests time out after 15 seconds and offer a retry. Data older than 30 days
is explicitly labeled historical; selected fighter details remain visible below
search for touch and keyboard users.
