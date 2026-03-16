var svg = d3.select('#graph');
var width = +svg.attr('width');
var height = +svg.attr('height');

var activeDataset = null;
var datasetCache = {};
var NODE_RADIUS = 12;
var currentNodeRadius = NODE_RADIUS;
var LINK_GAP = 6;
var INITIAL_LAYOUT_TICKS = 220;
var ZOOM_SCALE_EXTENT = [0.5, 4];
var DATASET_REQUEST_VERSION = Date.now();
var currentZoomTransform = d3.zoomIdentity;
var selectedFighterId = null;
var shouldAutoFocusSelection = false;
var currentFilteredNetwork = null;
var currentRenderedGraph = null;
var currentSearchMatches = [];
var currentSearchIndex = -1;
var MAX_SEARCH_RESULTS = 8;
var DISPLAY_COUNTRY_CODE_OVERRIDES = {
	EN: 'gb',
	SF: 'gb',
	WL: 'gb'
};

function parseDateValue(dateValue) {
	if (!dateValue) {
		return null;
	}

	var parsedDate = new Date(dateValue);
	if (Number.isNaN(parsedDate.getTime())) {
		parsedDate = new Date(dateValue + 'T00:00:00Z');
	}

	if (Number.isNaN(parsedDate.getTime())) {
		return null;
	}

	return parsedDate;
}

function formatDate(dateValue) {
	var parsedDate = parseDateValue(dateValue);
	if (!parsedDate) {
		return dateValue;
	}

	return parsedDate.toLocaleDateString(undefined, {
		year: 'numeric',
		month: 'long',
		day: 'numeric',
		timeZone: 'UTC'
	});
}

function resetTooltips() {
	d3.selectAll('.d3-tip').remove();
}

function getSelectedDataset() {
	return d3.select('#weightSelect').property('value');
}

function getSelectedRosterFilter() {
	return d3.select('#rosterFilter').property('value');
}

function shouldShowRankedLabels() {
	return getSelectedRosterFilter() === 'ranked';
}

function getSelectedMinFightCount() {
	return Number(d3.select('#minFightCount').property('value'));
}

function getSearchInput() {
	return document.getElementById('fighterSearch');
}

function getSearchResultsContainer() {
	return document.getElementById('fighterSearchResults');
}

function getSearchNote() {
	return document.getElementById('fighterSearchNote');
}

function getSearchClearButton() {
	return document.getElementById('fighterSearchClear');
}

function getNodeLabel(node) {
	return node.label || node.id;
}

function getNormalizedSearchValue(value) {
	return String(value || '').trim().toLowerCase();
}

function getSearchMeta(node) {
	var parts = [node.fightCount + ' fights'];

	if (node.isCurrentChampion) {
		parts.push('champion');
	} else if (node.isCurrentlyRanked) {
		parts.push('current #' + node.currentRank);
	} else if (node.isFormerChampion) {
		parts.push('former champion');
	} else if (node.isFormerTitleChallenger) {
		parts.push('former challenger');
	}

	if (node.countryName) {
		parts.push(node.countryName);
	}

	return parts.join(' • ');
}

function getSearchPriority(node, normalizedQuery) {
	var label = getNormalizedSearchValue(getNodeLabel(node));
	var nodeId = getNormalizedSearchValue(node.id);

	if (label === normalizedQuery) {
		return 0;
	}
	if (label.indexOf(normalizedQuery) === 0) {
		return 1;
	}
	if (label.indexOf(' ' + normalizedQuery) !== -1) {
		return 2;
	}
	if (nodeId === normalizedQuery) {
		return 3;
	}
	return 4;
}

function buildSearchMatches(filteredNetwork, query) {
	var normalizedQuery = getNormalizedSearchValue(query);
	if (!filteredNetwork || !normalizedQuery) {
		return [];
	}

	return filteredNetwork.nodes
		.filter(function(node) {
			var label = getNormalizedSearchValue(getNodeLabel(node));
			var nodeId = getNormalizedSearchValue(node.id);
			return label.indexOf(normalizedQuery) !== -1 || nodeId.indexOf(normalizedQuery) !== -1;
		})
		.sort(function(a, b) {
			var priorityDiff = getSearchPriority(a, normalizedQuery) - getSearchPriority(b, normalizedQuery);
			if (priorityDiff !== 0) {
				return priorityDiff;
			}

			var labelLengthDiff = getNodeLabel(a).length - getNodeLabel(b).length;
			if (labelLengthDiff !== 0) {
				return labelLengthDiff;
			}

			return getNodeLabel(a).localeCompare(getNodeLabel(b));
		})
		.slice(0, MAX_SEARCH_RESULTS);
}

function renderSearchResults(matches, shouldShowMatches) {
	var resultsContainer = getSearchResultsContainer();
	resultsContainer.innerHTML = '';

	if (!shouldShowMatches) {
		return;
	}

	if (!matches.length) {
		var emptyState = document.createElement('div');
		emptyState.className = 'searchEmptyState';
		emptyState.textContent = 'No visible fighters match this search.';
		resultsContainer.appendChild(emptyState);
		return;
	}

	matches.forEach(function(node, index) {
		var resultButton = document.createElement('button');
		var resultName = document.createElement('span');
		var resultMeta = document.createElement('span');

		resultButton.type = 'button';
		resultButton.className = 'searchResult';
		resultButton.setAttribute('role', 'option');
		resultButton.setAttribute('aria-selected', index === currentSearchIndex ? 'true' : 'false');
		if (index === currentSearchIndex) {
			resultButton.classList.add('searchResult-active');
		}

		resultName.className = 'searchResultName';
		resultName.textContent = getNodeLabel(node);

		resultMeta.className = 'searchResultMeta';
		resultMeta.textContent = getSearchMeta(node);

		resultButton.appendChild(resultName);
		resultButton.appendChild(resultMeta);
		resultButton.addEventListener('mousedown', function(event) {
			event.preventDefault();
		});
		resultButton.addEventListener('click', function() {
			selectSearchedFighter(node.id);
		});

		resultsContainer.appendChild(resultButton);
	});
}

function updateSearchNote(query, selectedNode, matchCount) {
	var searchNote = getSearchNote();

	if (selectedNode) {
		searchNote.textContent = 'Highlighting ' + getNodeLabel(selectedNode) + ' and direct matchup links.';
		return;
	}

	if (!query) {
		searchNote.textContent = 'Searches the fighters currently visible in this division view.';
		return;
	}

	if (!matchCount) {
		searchNote.textContent = 'No visible fighters match this search.';
		return;
	}

	searchNote.textContent = 'Select a fighter to spotlight their node, edges, and direct opponents.';
}

function syncSearchUI(filteredNetwork) {
	var searchInput = getSearchInput();
	var query = getNormalizedSearchValue(searchInput.value);
	var selectedNode = null;

	currentFilteredNetwork = filteredNetwork;

	if (selectedFighterId && filteredNetwork) {
		selectedNode = filteredNetwork.nodes.find(function(node) {
			return node.id === selectedFighterId;
		}) || null;
	}

	if (!selectedNode) {
		selectedFighterId = null;
	}

	if (selectedNode) {
		searchInput.value = getNodeLabel(selectedNode);
		query = getNormalizedSearchValue(searchInput.value);
		currentSearchMatches = [];
		currentSearchIndex = -1;
		renderSearchResults([], false);
	} else {
		currentSearchMatches = buildSearchMatches(filteredNetwork, query);
		if (currentSearchMatches.length === 0) {
			currentSearchIndex = -1;
		} else if (currentSearchIndex >= currentSearchMatches.length) {
			currentSearchIndex = 0;
		}
		renderSearchResults(currentSearchMatches, !!query);
	}

	updateSearchNote(query, selectedNode, currentSearchMatches.length);
	getSearchClearButton().disabled = !query && !selectedNode;
}

function updateRenderedSpotlight(options) {
	var spotlightState;
	var spotlightTransform;

	if (!currentRenderedGraph || !currentFilteredNetwork) {
		return false;
	}

	spotlightState = getSpotlightState(currentFilteredNetwork, currentRenderedGraph.renderLinks);
	applySpotlightClasses(
		currentRenderedGraph.nodeEnter,
		currentRenderedGraph.linkEnter,
		currentRenderedGraph.linkHitboxEnter,
		spotlightState
	);
	syncSearchUI(currentFilteredNetwork);

	if (options && options.autoFocus) {
		spotlightTransform = getSpotlightTransform(spotlightState, currentRenderedGraph.layoutSettings);
		if (spotlightTransform) {
			currentRenderedGraph.applyZoomTransform(spotlightTransform, true);
		}
	}

	return true;
}

function clearSearchSelection(options) {
	var searchInput = getSearchInput();
	var shouldRefocus = !options || options.refocus !== false;

	searchInput.value = '';
	currentSearchMatches = [];
	currentSearchIndex = -1;
	shouldAutoFocusSelection = false;

	if (selectedFighterId) {
		selectedFighterId = null;
		if (!updateRenderedSpotlight({ autoFocus: false })) {
			drawCurrentDataset();
		}
	} else {
		syncSearchUI(currentFilteredNetwork);
	}

	if (shouldRefocus) {
		searchInput.focus();
	}
}

function selectSearchedFighter(fighterId, options) {
	var selectedNode = currentFilteredNetwork && currentFilteredNetwork.nodes.find(function(node) {
		return node.id === fighterId;
	});

	if (!selectedNode) {
		return;
	}

	selectedFighterId = fighterId;
	shouldAutoFocusSelection = !!(options && options.autoFocus);
	currentSearchMatches = [];
	currentSearchIndex = -1;
	getSearchInput().value = getNodeLabel(selectedNode);
	if (!updateRenderedSpotlight({ autoFocus: shouldAutoFocusSelection })) {
		drawCurrentDataset();
	}
	shouldAutoFocusSelection = false;

	if (options && options.focusInput) {
		getSearchInput().focus();
	}
}

function toggleSelectedFighter(fighterId) {
	if (selectedFighterId === fighterId) {
		clearSearchSelection({ refocus: false });
		return;
	}

	selectSearchedFighter(fighterId, { autoFocus: false });
}

function handleSearchInput() {
	currentSearchIndex = -1;

	if (selectedFighterId) {
		selectedFighterId = null;
		shouldAutoFocusSelection = false;
		if (!updateRenderedSpotlight({ autoFocus: false })) {
			drawCurrentDataset();
		}
		return;
	}

	syncSearchUI(currentFilteredNetwork);
}

function handleSearchKeydown(event) {
	if (event.key === 'Escape') {
		event.preventDefault();
		clearSearchSelection();
		return;
	}

	if (!currentSearchMatches.length) {
		return;
	}

	if (event.key === 'ArrowDown') {
		event.preventDefault();
		currentSearchIndex = currentSearchIndex < currentSearchMatches.length - 1 ? currentSearchIndex + 1 : 0;
		renderSearchResults(currentSearchMatches, true);
		return;
	}

	if (event.key === 'ArrowUp') {
		event.preventDefault();
		currentSearchIndex = currentSearchIndex > 0 ? currentSearchIndex - 1 : currentSearchMatches.length - 1;
		renderSearchResults(currentSearchMatches, true);
		return;
	}

	if (event.key === 'Enter') {
		event.preventDefault();
		selectSearchedFighter(currentSearchMatches[currentSearchIndex >= 0 ? currentSearchIndex : 0].id);
	}
}

function getLinkEndpointId(endpoint) {
	return typeof endpoint === 'object' ? endpoint.id : endpoint;
}

function matchesRosterFilter(node, rosterFilter) {
	switch (rosterFilter) {
		case 'ranked':
			return !!node.isCurrentChampion || !!node.isCurrentlyRanked;
		case 'legacy':
			return !!node.isFormerChampion || !!node.isFormerTitleChallenger;
		default:
			return true;
	}
}

function applyFilters(dataset) {
	var rosterFilter = getSelectedRosterFilter();
	var minFightCount = getSelectedMinFightCount();

	var visibleNodeIds = new Set(
		dataset.nodes
			.filter(function(node) {
				return node.fightCount >= minFightCount && matchesRosterFilter(node, rosterFilter);
			})
			.map(function(node) {
				return node.id;
			})
	);

	var filteredNodes = dataset.nodes
		.filter(function(node) {
			return visibleNodeIds.has(node.id);
		})
		.map(function(node) {
			return Object.assign({}, node);
		});

	var filteredLinks = dataset.links
		.filter(function(link) {
			return (
				visibleNodeIds.has(getLinkEndpointId(link.source)) &&
				visibleNodeIds.has(getLinkEndpointId(link.target))
			);
		})
		.map(function(link) {
			return {
				source: getLinkEndpointId(link.source),
				target: getLinkEndpointId(link.target),
				value: link.value,
				bouts: Array.isArray(link.bouts) ? link.bouts.slice() : []
			};
		});

	return {
		nodes: filteredNodes,
		links: filteredLinks
	};
}

function describeRosterFilter() {
	var rosterFilter = getSelectedRosterFilter();

	switch (rosterFilter) {
		case 'ranked':
			return 'current Top 15 plus champion';
		case 'legacy':
			return 'former champs and title challengers in this division';
		default:
			return 'all fighters';
	}
}

function updateSourceNote(dataset, filteredNetwork) {
	var sourceNote = document.getElementById('sourceNote');
	var filterSummary = document.getElementById('filterSummary');
	var filteredBoutCount = filteredNetwork.links.reduce(function(total, link) {
		return total + link.value;
	}, 0);
	var minFightCount = getSelectedMinFightCount();
	var summaryParts = ['Showing ' + filteredNetwork.nodes.length + ' of ' + dataset.nodes.length + ' fighters'];

	if (filteredNetwork.links.length > 0) {
		summaryParts.push(filteredNetwork.links.length + ' matchups');
		summaryParts.push(filteredBoutCount + ' total bouts');
	} else if (filteredNetwork.nodes.length > 0) {
		summaryParts.push('no shared matchups under this filter');
	} else {
		summaryParts.push('no fighters match this filter');
	}

	summaryParts.push('roster: ' + describeRosterFilter());
	if (minFightCount > 0) {
		summaryParts.push('minimum ' + minFightCount + ' UFC fights in this division');
	}

	filterSummary.textContent = summaryParts.join(' • ');

	var sourceParts = [
		'Fight results: ' + dataset.meta.source_name,
		'coverage through ' + formatDate(dataset.meta.latest_resolved_event_date)
	];

	if (dataset.meta.rankings_updated_text) {
		sourceParts.push(
			'champion and ranking snapshot: ' +
			dataset.meta.ranking_source_name +
			' (' +
			dataset.meta.rankings_updated_text +
			')'
		);
	} else {
		sourceParts.push('champion and ranking snapshot: ' + dataset.meta.ranking_source_name);
	}

	sourceNote.textContent = sourceParts.join(' • ');
}

function renderEmptyState(message) {
	svg
		.append('text')
		.attr('class', 'empty-state')
		.attr('x', width / 2)
		.attr('y', height / 2)
		.attr('text-anchor', 'middle')
		.text(message);
}

function buildTooltipHtml(node) {
	var lines = ['<strong>' + (node.label || node.id) + '</strong>'];

	if (node.isCurrentChampion) {
		lines.push('Current champion');
	}
	if (node.isCurrentlyRanked) {
		lines.push('Current UFC rank: #' + node.currentRank);
	}
	if (node.isFormerChampion) {
		lines.push('Former champion in this division');
	} else if (node.isFormerTitleChallenger) {
		lines.push('Former title challenger in this division');
	}

	if (node.countryName) {
		lines.push('Flag: ' + node.countryName);
	} else {
		lines.push('Flag: unavailable in UFC athlete data');
	}

	lines.push(node.fightCount + ' UFC fights in this division');
	lines.push(node.uniqueOpponentCount + ' unique opponents');
	return lines.join('<br>');
}

function buildLinkTooltipHtml(renderLink) {
	var link = renderLink.link;
	var sourceLabel = link.source && link.source.label ? link.source.label : link.source.id;
	var targetLabel = link.target && link.target.label ? link.target.label : link.target.id;
	var bouts = Array.isArray(link.bouts) ? link.bouts : [];
	var lines = ['<strong>' + sourceLabel + ' vs ' + targetLabel + '</strong>'];

	lines.push(bouts.length + ' recorded bout' + (bouts.length === 1 ? '' : 's'));

	bouts.forEach(function(bout) {
		var boutParts = [];
		var eventLine = [];

		if (bout.resultSummary) {
			boutParts.push(bout.resultSummary);
		}
		if (bout.method) {
			boutParts.push('via ' + bout.method);
		}
		if (bout.isTitleBout) {
			boutParts.push('title bout');
		}
		if (bout.round || bout.time) {
			eventLine.push('R' + (bout.round || '?'));
			if (bout.time) {
				eventLine.push(bout.time);
			}
		}
		if (bout.eventName) {
			eventLine.push(bout.eventName);
		}
		if (bout.eventDate) {
			eventLine.push(formatDate(bout.eventDate));
		}

		lines.push(boutParts.concat(eventLine).join(' • '));
	});

	return lines.join('<br>');
}

function getNodeRadius(node) {
	return currentNodeRadius;
}

function getAdaptiveNodeRadius(nodeCount) {
	if (nodeCount <= 10) {
		return 18;
	}
	if (nodeCount <= 18) {
		return 16;
	}
	if (nodeCount <= 30) {
		return 14;
	}
	return NODE_RADIUS;
}

function getLayoutSettings(filteredNetwork) {
	var nodeCount = filteredNetwork.nodes.length;
	var nodeDegreeById = {};

	filteredNetwork.nodes.forEach(function(node) {
		nodeDegreeById[node.id] = 0;
	});
	filteredNetwork.links.forEach(function(link) {
		nodeDegreeById[link.source] = (nodeDegreeById[link.source] || 0) + 1;
		nodeDegreeById[link.target] = (nodeDegreeById[link.target] || 0) + 1;
	});
	filteredNetwork.nodes.forEach(function(node) {
		node.degree = nodeDegreeById[node.id] || 0;
	});

	return {
		nodeRadius: getAdaptiveNodeRadius(nodeCount),
		linkDistance: nodeCount <= 10 ? 112 : nodeCount <= 18 ? 94 : nodeCount <= 30 ? 70 : 56,
		chargeStrength: nodeCount <= 10 ? -212 : nodeCount <= 18 ? -168 : nodeCount <= 30 ? -112 : -95,
		centerStrength: nodeCount <= 10 ? 0.035 : nodeCount <= 18 ? 0.045 : nodeCount <= 30 ? 0.07 : 0.05,
		isolatedPullStrength: nodeCount <= 10 ? 0.08 : nodeCount <= 18 ? 0.1 : nodeCount <= 30 ? 0.14 : 0.12,
		collisionPadding: nodeCount <= 10 ? 22 : nodeCount <= 18 ? 16 : nodeCount <= 30 ? 11 : 7,
		alphaDecay: nodeCount <= 18 ? 0.078 : nodeCount <= 30 ? 0.07 : 0.05,
		fitPadding: nodeCount <= 18 ? 130 : nodeCount <= 30 ? 110 : 90,
		maxAutoScale: nodeCount <= 10 ? 2.2 : nodeCount <= 18 ? 2 : nodeCount <= 30 ? 1.8 : 1.3,
		needsAutoFit: nodeCount <= 40
	};
}

function seedNodePositions(nodes) {
	if (!nodes.length) {
		return;
	}

	var centerX = width / 2;
	var centerY = height / 2;
	var ringRadius = Math.min(width, height) * (nodes.length <= 10 ? 0.31 : nodes.length <= 18 ? 0.22 : 0.12);

	nodes.forEach(function(node, index) {
		if (typeof node.x === 'number' && typeof node.y === 'number') {
			return;
		}

		var angle = (index / Math.max(nodes.length, 1)) * Math.PI * 2;
		var distance = ringRadius + (index % 4) * (nodes.length <= 18 ? 30 : 12);
		node.x = centerX + Math.cos(angle) * distance;
		node.y = centerY + Math.sin(angle) * distance;
	});
}

function getAutoFitTransform(nodes, layoutSettings) {
	if (!nodes.length) {
		return d3.zoomIdentity;
	}

	if (nodes.length === 1) {
		return d3.zoomIdentity
			.translate(width / 2, height / 2)
			.scale(Math.min(layoutSettings.maxAutoScale, 2.6))
			translate(-nodes[0].x, -nodes[0].y);
	}

	var minX = Infinity;
	var minY = Infinity;
	var maxX = -Infinity;
	var maxY = -Infinity;
	var nodePadding = currentNodeRadius + 12;

	nodes.forEach(function(node) {
		minX = Math.min(minX, node.x - nodePadding);
		minY = Math.min(minY, node.y - nodePadding);
		maxX = Math.max(maxX, node.x + nodePadding);
		maxY = Math.max(maxY, node.y + nodePadding);
	});

	var boundsWidth = Math.max(maxX - minX, 1);
	var boundsHeight = Math.max(maxY - minY, 1);
	var scale = Math.min(
		layoutSettings.maxAutoScale,
		Math.min(
			(width - layoutSettings.fitPadding * 2) / boundsWidth,
			(height - layoutSettings.fitPadding * 2) / boundsHeight
		)
	);

	scale = Math.max(scale, ZOOM_SCALE_EXTENT[0]);

	var centerX = (minX + maxX) / 2;
	var centerY = (minY + maxY) / 2;

	return d3.zoomIdentity
		.translate(width / 2, height / 2)
		.scale(scale)
		translate(-centerX, -centerY);
}

function normalizeNodePositions(nodes, layoutSettings) {
	if (!nodes.length) {
		return;
	}

	var minX = Infinity;
	var minY = Infinity;
	var maxX = -Infinity;
	var maxY = -Infinity;
	var nodePadding = currentNodeRadius + 12;
	var scale;
	var centerX;
	var centerY;

	nodes.forEach(function(node) {
		minX = Math.min(minX, node.x - nodePadding);
		minY = Math.min(minY, node.y - nodePadding);
		maxX = Math.max(maxX, node.x + nodePadding);
		maxY = Math.max(maxY, node.y + nodePadding);
	});

	scale = Math.min(
		layoutSettings.maxAutoScale,
		Math.min(
			(width - layoutSettings.fitPadding * 2) / Math.max(maxX - minX, 1),
			(height - layoutSettings.fitPadding * 2) / Math.max(maxY - minY, 1)
		)
	);
	scale = Math.max(scale, 1);

	centerX = (minX + maxX) / 2;
	centerY = (minY + maxY) / 2;

	nodes.forEach(function(node) {
		node.x = (node.x - centerX) * scale + width / 2;
		node.y = (node.y - centerY) * scale + height / 2;
		node.vx = 0;
		node.vy = 0;
		if (node.fx != null) {
			node.fx = node.x;
		}
		if (node.fy != null) {
			node.fy = node.y;
		}
	});
}

function getDisplayCountryCode(node) {
	var code = node.countryCode;
	if (!code) {
		return null;
	}

	code = String(code).toUpperCase();
	return DISPLAY_COUNTRY_CODE_OVERRIDES[code] || code.toLowerCase();
}

function getNodeFlagUrl(node) {
	var code = getDisplayCountryCode(node);
	if (!code) {
		return null;
	}

	return 'flags/' + code + '.png';
}

function getNodeClipId(node) {
	return 'node-flag-clip-' + node.id;
}

function getNodeFallbackLabel(node) {
	if (node.countryCode) {
		return String(node.countryCode).toUpperCase();
	}
	return '?';
}

function getNodeTileSize(node) {
	return getNodeRadius(node) * 2;
}

function getNodeCornerRadius(node) {
	return Math.max(5, Math.round(getNodeRadius(node) * 0.46));
}

function getNodeFlagInset(node) {
	return Math.max(1, Math.round(getNodeRadius(node) * 0.08));
}

function buildRenderLinks(links) {
	return links.reduce(function(renderLinks, link) {
		var lineCount = Math.max(1, Number(link.value) || 1);
		for (var index = 0; index < lineCount; index += 1) {
			renderLinks.push({
				id: link.source + '-' + link.target + '-' + index,
				link: link,
				offset: (index - (lineCount - 1) / 2) * LINK_GAP
			});
		}
		return renderLinks;
	}, []);
}

function getSpotlightState(filteredNetwork, renderLinks) {
	var selectedNode;
	var neighborIds;
	var highlightLinkIds;

	if (!selectedFighterId) {
		return null;
	}

	selectedNode = filteredNetwork.nodes.find(function(node) {
		return node.id === selectedFighterId;
	});
	if (!selectedNode) {
		return null;
	}

	neighborIds = new Set();
	highlightLinkIds = new Set();

	filteredNetwork.links.forEach(function(link) {
		var sourceId = getLinkEndpointId(link.source);
		var targetId = getLinkEndpointId(link.target);

		if (sourceId === selectedFighterId) {
			neighborIds.add(targetId);
			return;
		}

		if (targetId === selectedFighterId) {
			neighborIds.add(sourceId);
		}
	});

	renderLinks.forEach(function(renderLink) {
		var sourceId = getLinkEndpointId(renderLink.link.source);
		var targetId = getLinkEndpointId(renderLink.link.target);

		if (sourceId === selectedFighterId || targetId === selectedFighterId) {
			highlightLinkIds.add(renderLink.id);
		}
	});

	return {
		selectedId: selectedFighterId,
		selectedNode: selectedNode,
		neighborIds: neighborIds,
		highlightLinkIds: highlightLinkIds,
		focusNodes: filteredNetwork.nodes.filter(function(node) {
			return node.id === selectedFighterId || neighborIds.has(node.id);
		})
	};
}

function applySpotlightClasses(nodeEnter, linkEnter, linkHitboxEnter, spotlightState) {
	var hasSpotlight = !!spotlightState;

	nodeEnter
		.classed('node-selected', function(d) {
			return hasSpotlight && d.id === spotlightState.selectedId;
		})
		.classed('node-neighbor', function(d) {
			return hasSpotlight && spotlightState.neighborIds.has(d.id);
		})
		.classed('node-dimmed', function(d) {
			return hasSpotlight && d.id !== spotlightState.selectedId && !spotlightState.neighborIds.has(d.id);
		});

	linkEnter
		.classed('link-selected', function(d) {
			return hasSpotlight && spotlightState.highlightLinkIds.has(d.id);
		})
		.classed('link-dimmed', function(d) {
			return hasSpotlight && !spotlightState.highlightLinkIds.has(d.id);
		});

	linkHitboxEnter.classed('link-dimmed', function(d) {
		return hasSpotlight && !spotlightState.highlightLinkIds.has(d.id);
	});
}

function getSpotlightTransform(spotlightState, layoutSettings) {
	if (!spotlightState || !spotlightState.focusNodes.length) {
		return null;
	}

	return getAutoFitTransform(
		spotlightState.focusNodes,
		Object.assign({}, layoutSettings, {
			fitPadding: Math.max(layoutSettings.fitPadding, 140),
			maxAutoScale: Math.max(layoutSettings.maxAutoScale, 2.35)
		})
	);
}

function getLinkCoordinates(renderLink) {
	var source = renderLink.link.source;
	var target = renderLink.link.target;
	var offset = renderLink.offset;
	var x1 = source.x;
	var y1 = source.y;
	var x2 = target.x;
	var y2 = target.y;

	if (!offset) {
		return { x1: x1, y1: y1, x2: x2, y2: y2 };
	}

	var dx = x2 - x1;
	var dy = y2 - y1;
	var length = Math.sqrt(dx * dx + dy * dy) || 1;
	var normalX = -dy / length;
	var normalY = dx / length;

	return {
		x1: x1 + normalX * offset,
		y1: y1 + normalY * offset,
		x2: x2 + normalX * offset,
		y2: y2 + normalY * offset
	};
}

function drawCurrentDataset() {
	resetTooltips();
	currentRenderedGraph = null;
	svg.selectAll('*').remove();

	if (!activeDataset) {
		return;
	}

	var filteredNetwork = applyFilters(activeDataset);
	var renderLinks = buildRenderLinks(filteredNetwork.links);
	var spotlightState = getSpotlightState(filteredNetwork, renderLinks);
	updateSourceNote(activeDataset, filteredNetwork);
	syncSearchUI(filteredNetwork);

	if (filteredNetwork.nodes.length === 0) {
		renderEmptyState('No fighters match the current filters.');
		return;
	}

	var layoutSettings = getLayoutSettings(filteredNetwork);
	currentNodeRadius = layoutSettings.nodeRadius;
	currentZoomTransform = d3.zoomIdentity;
	seedNodePositions(filteredNetwork.nodes);

	var defs = svg.append('defs');
	var viewportG = svg.append('g').attr('class', 'graph-viewport');
	var linkG = viewportG.append('g').attr('class', 'links-group');
	var nodeG = viewportG.append('g').attr('class', 'nodes-group');

	var simulation = d3.forceSimulation()
		.alphaDecay(layoutSettings.alphaDecay)
		.force(
			'link',
			d3.forceLink().id(function(d) {
				return d.id;
			}).distance(layoutSettings.linkDistance)
		)
		.force('charge', d3.forceManyBody().strength(layoutSettings.chargeStrength))
		.force(
			'collision',
			d3.forceCollide().radius(currentNodeRadius + layoutSettings.collisionPadding).strength(0.95)
		)
		.force(
			'x',
			d3.forceX(width / 2).strength(function(d) {
				return d.degree === 0 ? layoutSettings.isolatedPullStrength : layoutSettings.centerStrength;
			})
		)
		.force(
			'y',
			d3.forceY(height / 2).strength(function(d) {
				return d.degree === 0 ? layoutSettings.isolatedPullStrength : layoutSettings.centerStrength;
			})
		)
		.force('center', d3.forceCenter(width / 2, height / 2));

	var drag = d3.drag()
		.on('start', dragstarted)
		.on('drag', dragged)
		.on('end', dragended);

	var zoom = d3.zoom()
		.scaleExtent(ZOOM_SCALE_EXTENT)
		.filter(function() {
			var event = d3.event;
			if (!event) {
				return false;
			}

			if (event.type === 'wheel') {
				return !!event.metaKey;
			}

			return false;
		})
		.on('zoom', zoomed);

	var linkEnter = linkG.selectAll('.link')
		.data(renderLinks)
		.enter()
		.append('line')
		.attr('class', 'link')
		.attr('data-link-id', function(d) {
			return d.id;
		});

	var linkHitboxEnter = linkG.selectAll('.link-hitbox')
		.data(renderLinks)
		.enter()
		.append('line')
		.attr('class', 'link-hitbox')
		.attr('data-link-id', function(d) {
			return d.id;
		});

	var nodeEnter = nodeG.selectAll('.node-group')
		.data(filteredNetwork.nodes)
		.enter()
		.append('g')
		.attr('class', 'node-group');

	defs.selectAll('.node-clip')
		.data(filteredNetwork.nodes)
		.enter()
		.append('clipPath')
		.attr('class', 'node-clip')
		.attr('id', function(d) {
			return getNodeClipId(d);
		})
		.append('rect')
		.attr('x', function(d) {
			return -getNodeRadius(d) + 1;
		})
		.attr('y', function(d) {
			return -getNodeRadius(d) + 1;
		})
		.attr('width', function(d) {
			return getNodeTileSize(d) - 2;
		})
		.attr('height', function(d) {
			return getNodeTileSize(d) - 2;
		})
		.attr('rx', function(d) {
			return getNodeCornerRadius(d) - 1;
		})
		.attr('ry', function(d) {
			return getNodeCornerRadius(d) - 1;
		});

	nodeEnter
		.filter(function(d) {
			return d.isCurrentlyRanked && !d.isCurrentChampion;
		})
		.append('rect')
		.attr('class', 'node-ring node-ring-ranked')
		.attr('x', function(d) {
			return -(getNodeRadius(d) + 3);
		})
		.attr('y', function(d) {
			return -(getNodeRadius(d) + 3);
		})
		.attr('width', function(d) {
			return getNodeTileSize(d) + 6;
		})
		.attr('height', function(d) {
			return getNodeTileSize(d) + 6;
		})
		.attr('rx', function(d) {
			return getNodeCornerRadius(d) + 3;
		})
		.attr('ry', function(d) {
			return getNodeCornerRadius(d) + 3;
		});

	nodeEnter
		.filter(function(d) {
			return d.isCurrentChampion;
		})
		.append('rect')
		.attr('class', 'node-ring node-ring-champion')
		.attr('x', function(d) {
			return -(getNodeRadius(d) + 6);
		})
		.attr('y', function(d) {
			return -(getNodeRadius(d) + 6);
		})
		.attr('width', function(d) {
			return getNodeTileSize(d) + 12;
		})
		.attr('height', function(d) {
			return getNodeTileSize(d) + 12;
		})
		.attr('rx', function(d) {
			return getNodeCornerRadius(d) + 6;
		})
		.attr('ry', function(d) {
			return getNodeCornerRadius(d) + 6;
		});

	nodeEnter
		.filter(function(d) {
			return d.isFormerChampion && !d.isCurrentChampion;
		})
		.append('rect')
		.attr('class', 'node-ring node-ring-former')
		.attr('x', function(d) {
			return -(getNodeRadius(d) + (d.isCurrentlyRanked ? 6 : 3));
		})
		.attr('y', function(d) {
			return -(getNodeRadius(d) + (d.isCurrentlyRanked ? 6 : 3));
		})
		.attr('width', function(d) {
			return getNodeTileSize(d) + (d.isCurrentlyRanked ? 12 : 6);
		})
		.attr('height', function(d) {
			return getNodeTileSize(d) + (d.isCurrentlyRanked ? 12 : 6);
		})
		.attr('rx', function(d) {
			return getNodeCornerRadius(d) + (d.isCurrentlyRanked ? 6 : 3);
		})
		.attr('ry', function(d) {
			return getNodeCornerRadius(d) + (d.isCurrentlyRanked ? 6 : 3);
		});

	nodeEnter
		.append('rect')
		.attr('class', 'node-shell')
		.attr('x', function(d) {
			return -getNodeRadius(d);
		})
		.attr('y', function(d) {
			return -getNodeRadius(d);
		})
		.attr('width', function(d) {
			return getNodeTileSize(d);
		})
		.attr('height', function(d) {
			return getNodeTileSize(d);
		})
		.attr('rx', function(d) {
			return getNodeCornerRadius(d);
		})
		.attr('ry', function(d) {
			return getNodeCornerRadius(d);
		});

	nodeEnter
		.filter(function(d) {
			return !!getNodeFlagUrl(d);
		})
		.append('image')
		.attr('class', 'node-flag-image')
		.attr('x', function(d) {
			return -getNodeRadius(d) + getNodeFlagInset(d);
		})
		.attr('y', function(d) {
			return -getNodeRadius(d) + getNodeFlagInset(d);
		})
		.attr('width', function(d) {
			return getNodeTileSize(d) - getNodeFlagInset(d) * 2;
		})
		.attr('height', function(d) {
			return getNodeTileSize(d) - getNodeFlagInset(d) * 2;
		})
		.attr('preserveAspectRatio', 'xMidYMid meet')
		.attr('clip-path', function(d) {
			return 'url(#' + getNodeClipId(d) + ')';
		})
		.attr('href', function(d) {
			return getNodeFlagUrl(d);
		})
		.attr('xlink:href', function(d) {
			return getNodeFlagUrl(d);
		});

	nodeEnter
		.filter(function(d) {
			return !getNodeFlagUrl(d);
		})
		.append('text')
		.attr('class', 'node-flag-fallback')
		.attr('text-anchor', 'middle')
		.attr('dy', '0.34em')
		.text(function(d) {
			return getNodeFallbackLabel(d);
		});

	if (shouldShowRankedLabels()) {
		var labelEnter = nodeEnter
			.append('g')
			.attr('class', 'node-label')
			.attr('transform', function(d) {
				return 'translate(0,' + (-(getNodeRadius(d) + 18)) + ')';
			});

		labelEnter
			.append('rect')
			.attr('class', 'node-label-chip');

		labelEnter
			.append('text')
			.attr('class', 'node-label-text')
			.attr('text-anchor', 'middle')
			.attr('dy', '0.34em')
			.text(function(d) {
				return getNodeLabel(d);
			});

		labelEnter.each(function() {
			var labelGroup = d3.select(this);
			var labelText = labelGroup.select('.node-label-text').node();
			var textBounds = labelText.getBBox();
			var horizontalPadding = 10;
			var verticalPadding = 5;

			labelGroup
				.select('.node-label-chip')
				.attr('x', textBounds.x - horizontalPadding)
				.attr('y', textBounds.y - verticalPadding)
				.attr('width', textBounds.width + horizontalPadding * 2)
				.attr('height', textBounds.height + verticalPadding * 2)
				.attr('rx', 10)
				.attr('ry', 10);
		});
	}

	applySpotlightClasses(nodeEnter, linkEnter, linkHitboxEnter, spotlightState);

	simulation.nodes(filteredNetwork.nodes).on('tick', tickSimulation);
	simulation.force('link').links(filteredNetwork.links);
	simulation.stop();
	for (var tickIndex = 0; tickIndex < INITIAL_LAYOUT_TICKS; tickIndex += 1) {
		simulation.tick();
	}
	normalizeNodePositions(filteredNetwork.nodes, layoutSettings);
	tickSimulation();

	if (spotlightState && shouldAutoFocusSelection) {
		currentZoomTransform = getSpotlightTransform(spotlightState, layoutSettings) || d3.zoomIdentity;
	} else {
		currentZoomTransform = d3.zoomIdentity;
	}
	shouldAutoFocusSelection = false;

	nodeEnter.call(drag);

	function tickSimulation() {
		linkHitboxEnter
			.each(function(d) {
				var coordinates = getLinkCoordinates(d);
				d3.select(this)
					.attr('x1', coordinates.x1)
					.attr('y1', coordinates.y1)
					.attr('x2', coordinates.x2)
					.attr('y2', coordinates.y2);
			});

		linkEnter
			.each(function(d) {
				var coordinates = getLinkCoordinates(d);
				d3.select(this)
					.attr('x1', coordinates.x1)
					.attr('y1', coordinates.y1)
					.attr('x2', coordinates.x2)
					.attr('y2', coordinates.y2);
			});

		nodeEnter
			.attr('transform', function(d) {
				return 'translate(' + d.x + ',' + d.y + ')';
			});
	}

	function dragstarted(d) {
		if (!d3.event.active) {
			simulation.alphaTarget(0.3).restart();
		}
		d.fx = d.x;
		d.fy = d.y;
	}

	function dragged(d) {
		d.fx = d3.event.x;
		d.fy = d3.event.y;
	}

	function dragended(d) {
		if (!d3.event.active) {
			simulation.alphaTarget(0);
		}
		d.fx = d3.event.x;
		d.fy = d3.event.y;
	}

	var tip = d3.tip()
		.attr('class', 'd3-tip')
		.offset([-15, 0])
		.html(function(d) {
			return buildTooltipHtml(d);
		});
	var linkTip = d3.tip()
		.attr('class', 'd3-tip d3-tip-link')
		.offset([-12, 0])
		.html(function(d) {
			return buildLinkTooltipHtml(d);
		});

	svg.call(zoom).on('dblclick.zoom', null);
	applyZoomTransform(currentZoomTransform, false);
	svg.call(tip);
	svg.call(linkTip);
	svg.on('click.selection', function() {
		var event = d3.event;
		var target = event && event.target;

		if (event && event.defaultPrevented) {
			return;
		}

		if (target && typeof target.closest === 'function' && target.closest('.node-group')) {
			return;
		}

		if (selectedFighterId) {
			clearSearchSelection({ refocus: false });
		}
	});

	nodeEnter
		.on('click', function(d) {
			if (d3.event.defaultPrevented) {
				return;
			}

			d3.event.stopPropagation();
			toggleSelectedFighter(d.id);
		})
		.on('mouseover', tip.show)
		.on('mouseout', tip.hide);

	linkHitboxEnter
		.on('mouseover', function(d) {
			linkG.select('.link[data-link-id="' + d.id + '"]').classed('link-hovered', true);
			linkTip.show.call(this, d);
		})
		.on('mouseout', function(d) {
			linkG.select('.link[data-link-id="' + d.id + '"]').classed('link-hovered', false);
			linkTip.hide.call(this, d);
		});

	function zoomed() {
		currentZoomTransform = d3.event.transform;
		viewportG.attr('transform', currentZoomTransform);
	}

	function applyZoomTransform(transform, animate) {
		if (animate) {
			var startTransform = currentZoomTransform;
			var interpolateX = d3.interpolateNumber(startTransform.x, transform.x);
			var interpolateY = d3.interpolateNumber(startTransform.y, transform.y);
			var interpolateK = d3.interpolateNumber(startTransform.k, transform.k);

			svg
				.interrupt()
				.transition()
				.duration(180)
				.tween('viewport-transform', function() {
					return function(t) {
						currentZoomTransform = d3.zoomIdentity
							.translate(interpolateX(t), interpolateY(t))
							.scale(interpolateK(t));
						viewportG.attr('transform', currentZoomTransform);
						svg.property('__zoom', currentZoomTransform);
					};
				});
			return;
		}

		svg.interrupt();
		currentZoomTransform = transform;
		viewportG.attr('transform', currentZoomTransform);
		svg.property('__zoom', currentZoomTransform);
	}

	currentRenderedGraph = {
		filteredNetwork: filteredNetwork,
		renderLinks: renderLinks,
		layoutSettings: layoutSettings,
		nodeEnter: nodeEnter,
		linkEnter: linkEnter,
		linkHitboxEnter: linkHitboxEnter,
		applyZoomTransform: applyZoomTransform
	};
}

function loadDataset(filename) {
	if (datasetCache[filename]) {
		activeDataset = datasetCache[filename];
		drawCurrentDataset();
		return;
	}

	svg.selectAll('*').remove();
	renderEmptyState('Loading division data...');

	d3.json('data/' + filename + '?v=' + DATASET_REQUEST_VERSION).then(function(dataset) {
		datasetCache[filename] = dataset;
		activeDataset = dataset;
		drawCurrentDataset();
	});
}

function onWeightChanged() {
	loadDataset(getSelectedDataset());
}

function onFilterChanged() {
	drawCurrentDataset();
}

function initializeSearchControls() {
	getSearchInput().addEventListener('input', handleSearchInput);
	getSearchInput().addEventListener('keydown', handleSearchKeydown);
	getSearchClearButton().addEventListener('click', function() {
		clearSearchSelection();
	});
}

initializeSearchControls();
loadDataset(getSelectedDataset());
