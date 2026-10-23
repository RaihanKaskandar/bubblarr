const LogEls = {
	list: document.querySelector('#log-list'),
	empty: document.querySelector('#log-empty'),
	summary: document.querySelector('#log-summary'),
	level: document.querySelector('#level-input'),
	search: document.querySelector('#log-search-input'),
	refresh: document.querySelector('#refresh-button'),
	pause: document.querySelector('#pause-button'),
	pauseLabel: document.querySelector('#pause-label'),
	pauseIcon: document.querySelector('#pause-icon'),
	playIcon: document.querySelector('#play-icon'),
	download: document.querySelector('#download-button')
};

const LogState = {
	instance: null,
	lastId: 0,
	shown: 0,
	paused: false,
	timer: null,
	searchTimer: null,
	loading: false,
	generation: 0
};

// Newest entries are shown on top, like in Sonarr
const MAX_SHOWN = 2000;
const POLL_INTERVAL = 3000;

const timeFormat = new Intl.DateTimeFormat(undefined, {
	month: 'short', day: 'numeric',
	hour: '2-digit', minute: '2-digit', second: '2-digit'
});

function formatTime(iso) {
	const date = new Date(iso);
	return isNaN(date) ? iso : timeFormat.format(date);
};

function buildRow(entry) {
	const row = document.createElement('tr');
	row.classList.add(`log-${entry.level.toLowerCase()}`);

	const level = document.createElement('td');
	level.classList.add('log-level-column');
	const badge = document.createElement('span');
	badge.classList.add('log-badge');
	badge.title = entry.level;
	badge.innerText = entry.level.charAt(0);
	level.appendChild(badge);

	const time = document.createElement('td');
	time.classList.add('log-time-column');
	time.innerText = formatTime(entry.time);
	time.title = entry.time;

	const message = document.createElement('td');
	message.classList.add('log-message');
	const lines = entry.message.split('\n');
	const first = document.createElement('span');
	first.innerText = lines[0];
	message.appendChild(first);
	if (lines.length > 1) {
		// Tracebacks and other long messages are folded
		const details = document.createElement('details');
		const summary = document.createElement('summary');
		summary.innerText = `${lines.length - 1} more lines`;
		const rest = document.createElement('pre');
		rest.innerText = lines.slice(1).join('\n');
		details.append(summary, rest);
		message.appendChild(details);
	};

	const source = document.createElement('td');
	source.classList.add('log-source-column');
	source.innerText = entry.source;
	source.title = entry.thread;

	row.append(level, time, message, source);
	return row;
};

function updateSummary() {
	LogEls.empty.classList.toggle('hidden', LogState.shown > 0);
	LogEls.summary.innerText = LogState.paused
		? `Paused, showing ${LogState.shown} entries`
		: `Live, showing the newest ${LogState.shown} entries`;
};

function fetchEntries(api_key, reset=false) {
	// Polls are skipped while loading, a reset (new filter) always goes
	// through and makes the responses of older requests be ignored
	if (LogState.loading && !reset)
		return Promise.resolve();
	if (reset)
		LogState.generation++;
	const generation = LogState.generation;
	LogState.loading = true;

	const params = {
		level: LogEls.level.value,
		after: reset ? 0 : LogState.lastId,
		limit: reset ? 1000 : 500
	};
	const search = LogEls.search.value.trim();
	if (search)
		params.search = encodeURIComponent(search);

	return fetchAPI('/system/logs/entries', api_key, params)
	.then(json => {
		if (generation !== LogState.generation)
			return;
		const result = json.result;
		if (!reset && LogState.instance !== null && result.instance !== LogState.instance) {
			// Bubblarr restarted, so the IDs started over
			return fetchEntries(api_key, true);
		};

		if (reset) {
			LogEls.list.innerHTML = '';
			LogState.shown = 0;
		};
		LogState.instance = result.instance;
		LogState.lastId = result.last_id;

		const fragment = document.createDocumentFragment();
		for (let i = result.entries.length - 1; i >= 0; i--)
			fragment.appendChild(buildRow(result.entries[i]));
		LogEls.list.prepend(fragment);
		LogState.shown += result.entries.length;

		while (LogState.shown > MAX_SHOWN) {
			LogEls.list.lastElementChild.remove();
			LogState.shown--;
		};
		updateSummary();
	})
	.finally(() => {
		if (generation === LogState.generation)
			LogState.loading = false;
	});
};

function schedulePolling(api_key) {
	clearInterval(LogState.timer);
	if (!LogState.paused)
		LogState.timer = setInterval(() => {
			if (!document.hidden)
				fetchEntries(api_key);
		}, POLL_INTERVAL);
};

function setPaused(paused, api_key) {
	LogState.paused = paused;
	LogEls.pause.setAttribute('aria-pressed', paused);
	LogEls.pauseLabel.innerText = paused ? 'Resume' : 'Pause';
	LogEls.pauseIcon.classList.toggle('hidden', paused);
	LogEls.playIcon.classList.toggle('hidden', !paused);
	if (!paused)
		fetchEntries(api_key);
	schedulePolling(api_key);
	updateSummary();
};

// code run on load

try {
	const savedLevel = localStorage.getItem('logs_level');
	if (savedLevel)
		LogEls.level.value = savedLevel;
} catch (e) {};

usingApiKey()
.then(api_key => {
	LogEls.download.href = `${url_base}/api/system/logs?api_key=${api_key}`;
	fetchEntries(api_key, true);
	schedulePolling(api_key);

	LogEls.refresh.onclick = e => fetchEntries(api_key, true);
	LogEls.pause.onclick = e => setPaused(!LogState.paused, api_key);
	LogEls.level.onchange = e => {
		try { localStorage.setItem('logs_level', LogEls.level.value) } catch (e) {};
		fetchEntries(api_key, true);
	};
	LogEls.search.oninput = e => {
		clearTimeout(LogState.searchTimer);
		LogState.searchTimer = setTimeout(() => fetchEntries(api_key, true), 300);
	};
});
