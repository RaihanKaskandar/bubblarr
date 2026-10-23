const CalendarEls = {
	grid: document.querySelector('#calendar-grid'),
	agenda: document.querySelector('#calendar-agenda'),
	empty: document.querySelector('#calendar-empty'),
	pullListError: document.querySelector('#pull-list-error'),
	title: document.querySelector('#calendar-title'),
	previous: document.querySelector('#previous-button'),
	today: document.querySelector('#today-button'),
	next: document.querySelector('#next-button'),
	refresh: document.querySelector('#refresh-button'),
	monitoredOnly: document.querySelector('#monitored-only-input'),
	viewButtons: document.querySelectorAll('#view-switcher button')
};

const AGENDA_DAYS = 31;

const pullListErrors = {
	invalid_credentials: 'wrong username or password (see Settings > Metadata)',
	connection_error: "couldn't connect",
	failed_processing_response: 'unexpected response (their website might have changed)'
};
// Below this width the month and week grids don't fit, so use the agenda
const NARROW_SCREEN = 700;

let anchorDate = startOfDay(new Date());
let view = getLocalStorage('calendar_view')['calendar_view'] || 'month';
let loadCounter = 0;

//
// Date helpers (all in local time)
//
function startOfDay(date) {
	return new Date(date.getFullYear(), date.getMonth(), date.getDate());
};

function addDays(date, days) {
	return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
};

function startOfWeek(date) {
	// Weeks start on Monday
	return addDays(date, -((date.getDay() + 6) % 7));
};

function toISODate(date) {
	const pad = n => String(n).padStart(2, '0');
	return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
};

function parseISODate(value) {
	const [year, month, day] = value.split('-').map(Number);
	return new Date(year, month - 1, day);
};

function formatDate(date, options) {
	return date.toLocaleDateString(undefined, options);
};

//
// View handling
//
function effectiveView() {
	return window.innerWidth < NARROW_SCREEN ? 'agenda' : view;
};

function getRange() {
	const currentView = effectiveView();
	if (currentView === 'month') {
		const first = new Date(anchorDate.getFullYear(), anchorDate.getMonth(), 1);
		const start = startOfWeek(first);
		return {start: start, end: addDays(start, 41)};
	};
	if (currentView === 'week') {
		const start = startOfWeek(anchorDate);
		return {start: start, end: addDays(start, 6)};
	};
	return {start: anchorDate, end: addDays(anchorDate, AGENDA_DAYS - 1)};
};

function getTitle(range) {
	const currentView = effectiveView();
	if (currentView === 'month')
		return formatDate(anchorDate, {month: 'long', year: 'numeric'});

	const sameYear = range.start.getFullYear() === range.end.getFullYear();
	const startText = formatDate(range.start, {
		day: 'numeric', month: 'short', year: sameYear ? undefined : 'numeric'
	});
	const endText = formatDate(range.end, {
		day: 'numeric', month: 'short', year: 'numeric'
	});
	return `${startText} – ${endText}`;
};

function move(direction) {
	const currentView = effectiveView();
	if (currentView === 'month')
		anchorDate = new Date(
			anchorDate.getFullYear(), anchorDate.getMonth() + direction, 1
		);
	else if (currentView === 'week')
		anchorDate = addDays(anchorDate, 7 * direction);
	else
		anchorDate = addDays(anchorDate, AGENDA_DAYS * direction);
};

function setView(newView) {
	view = newView;
	setLocalStorage({'calendar_view': newView});
	CalendarEls.viewButtons.forEach(b =>
		b.classList.toggle('selected', b.dataset.view === newView)
	);
};

//
// Issue status
//
function getStatus(issue, downloadingIssues, today) {
	if (issue.not_in_library)
		return 'not-in-library';
	if (downloadingIssues.has(issue.issue_id))
		return 'downloading';
	if (issue.downloaded)
		return 'downloaded';
	if (!issue.monitored)
		return 'unmonitored';
	if (issue.date > today)
		return 'upcoming';
	return 'missing';
};

const statusText = {
	downloaded: 'Downloaded',
	downloading: 'Downloading',
	missing: 'Missing',
	upcoming: 'Upcoming',
	unmonitored: 'Unmonitored',
	'not-in-library': 'Not in library'
};

function issueLabel(issue) {
	return `${issue.volume_title} #${issue.issue_number}`;
};

function issueTooltip(issue, status) {
	let text = issue.not_in_library
		? `${issue.volume_title} #${issue.issue_number}`
		: `${issue.volume_title} (${issue.volume_year}) #${issue.issue_number}`;
	if (issue.title)
		text += `\n${issue.title}`;
	text += `\n${statusText[status]}`;
	if (issue.pulled)
		text += '\nOn your pull list';
	return text;
};

function issueLink(issue) {
	return issue.not_in_library
		? issue.url
		: `${url_base}/volumes/${issue.volume_id}`;
};

function pullMarker() {
	const marker = document.createElement('span');
	marker.classList.add('pull-marker');
	marker.innerText = 'Pull';
	marker.title = 'On your League of Comic Geeks pull list';
	return marker;
};

//
// Rendering
//
function createEvent(issue, status, showTitle) {
	const event = document.createElement('a');
	event.classList.add('calendar-event', `status-${status}`);
	event.href = issueLink(issue);
	if (issue.not_in_library)
		event.target = '_blank';
	event.title = issueTooltip(issue, status);

	const name = document.createElement('span');
	name.classList.add('event-volume');
	name.innerText = issue.volume_title;
	event.appendChild(name);

	const number = document.createElement('span');
	number.classList.add('event-number');
	number.innerText = `#${issue.issue_number}`;
	event.appendChild(number);

	if (issue.pulled)
		event.appendChild(pullMarker());

	if (showTitle && issue.title) {
		const title = document.createElement('span');
		title.classList.add('event-title');
		title.innerText = issue.title;
		event.appendChild(title);
	};

	return event;
};

function renderGrid(range, issuesByDate, downloadingIssues, today) {
	const currentView = effectiveView();
	const grid = CalendarEls.grid;
	grid.innerHTML = '';
	grid.classList.toggle('week-view', currentView === 'week');

	// Day names
	for (let i = 0; i < 7; i++) {
		const name = document.createElement('div');
		name.classList.add('day-name');
		name.innerText = formatDate(addDays(range.start, i), {weekday: 'short'});
		grid.appendChild(name);
	};

	for (
		let day = range.start;
		day <= range.end;
		day = addDays(day, 1)
	) {
		const isoDay = toISODate(day);
		const cell = document.createElement('div');
		cell.classList.add('day-cell');
		if (isoDay === today)
			cell.classList.add('today');
		if (currentView === 'month' && day.getMonth() !== anchorDate.getMonth())
			cell.classList.add('other-month');

		const number = document.createElement('div');
		number.classList.add('day-number');
		number.innerText = currentView === 'week'
			? formatDate(day, {day: 'numeric', month: 'short'})
			: day.getDate();
		cell.appendChild(number);

		const events = document.createElement('div');
		events.classList.add('day-events');
		(issuesByDate[isoDay] || []).forEach(issue => {
			const status = getStatus(issue, downloadingIssues, today);
			events.appendChild(createEvent(issue, status, currentView === 'week'));
		});
		cell.appendChild(events);

		grid.appendChild(cell);
	};
};

function renderAgenda(issuesByDate, downloadingIssues, today, api_key) {
	const agenda = CalendarEls.agenda;
	agenda.innerHTML = '';

	Object.keys(issuesByDate).sort().forEach(isoDay => {
		const section = document.createElement('section');
		section.classList.add('agenda-day');
		if (isoDay === today)
			section.classList.add('today');

		const header = document.createElement('h3');
		header.innerText = formatDate(parseISODate(isoDay), {
			weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
		});
		if (isoDay === today)
			header.innerText += ' (today)';
		section.appendChild(header);

		issuesByDate[isoDay].forEach(issue => {
			const status = getStatus(issue, downloadingIssues, today);

			const row = document.createElement('a');
			row.classList.add('agenda-entry', `status-${status}`);
			row.href = issueLink(issue);
			if (issue.not_in_library)
				row.target = '_blank';
			row.title = issueTooltip(issue, status);

			const cover = document.createElement('img');
			cover.classList.add('agenda-cover');
			cover.loading = 'lazy';
			cover.alt = '';
			if (!issue.not_in_library)
				cover.src = `${url_base}/api/volumes/${issue.volume_id}/cover?api_key=${api_key}`;
			row.appendChild(cover);

			const info = document.createElement('div');
			info.classList.add('agenda-info');
			const volume = document.createElement('p');
			volume.classList.add('agenda-volume');
			volume.innerText = issue.not_in_library
				? issue.volume_title
				: `${issue.volume_title} (${issue.volume_year})`;
			info.appendChild(volume);
			const issueText = document.createElement('p');
			issueText.classList.add('agenda-issue');
			issueText.innerText = `#${issue.issue_number}` + (issue.title ? ` – ${issue.title}` : '');
			info.appendChild(issueText);
			row.appendChild(info);

			if (issue.pulled)
				row.appendChild(pullMarker());

			const badge = document.createElement('span');
			badge.classList.add('agenda-status');
			badge.innerText = statusText[status];
			row.appendChild(badge);

			section.appendChild(row);
		});

		agenda.appendChild(section);
	});
};

function loadCalendar(api_key) {
	const loadId = ++loadCounter;
	const range = getRange();
	const currentView = effectiveView();
	CalendarEls.title.innerText = getTitle(range);
	CalendarEls.today.disabled = (
		range.start <= startOfDay(new Date())
		&& startOfDay(new Date()) <= range.end
		&& (currentView !== 'agenda' || toISODate(range.start) === toISODate(new Date()))
	);

	const params = {
		start: toISODate(range.start),
		end: toISODate(range.end)
	};
	if (CalendarEls.monitoredOnly.checked)
		params.monitored_only = 'true';

	const render = (calendar, queue, pullList) => {
		if (loadId !== loadCounter)
			// A newer load was started in the meantime
			return;

		const today = toISODate(new Date());
		const downloadingIssues = new Set(
			queue.result
				.filter(d => d.issue_id !== null && !['failed', 'canceled'].includes(d.status))
				.map(d => d.issue_id)
		);

		// Pulled issues: mark the ones in the library, add the others
		const pulledIssueIds = new Set();
		const notInLibrary = [];
		pullList.result.items.forEach(item => {
			if (item.issue_id !== null)
				pulledIssueIds.add(item.issue_id);
			else if (!CalendarEls.monitoredOnly.checked)
				notInLibrary.push({
					not_in_library: true,
					pulled: true,
					issue_id: null,
					volume_id: null,
					volume_title: item.series_title,
					volume_year: null,
					issue_number: item.issue_number || '?',
					title: null,
					date: item.date,
					url: item.url
				});
		});

		if (pullList.result.error) {
			CalendarEls.pullListError.innerText =
				"Couldn't load your League of Comic Geeks pull list: "
				+ (pullListErrors[pullList.result.error] || pullList.result.error);
			hide([], [CalendarEls.pullListError]);
		}
		else
			hide([CalendarEls.pullListError]);

		const issuesByDate = {};
		calendar.result.forEach(issue => {
			issue.pulled = issue.issue_id !== null && pulledIssueIds.has(issue.issue_id);
		});
		calendar.result.concat(notInLibrary).forEach(issue => {
			(issuesByDate[issue.date] = issuesByDate[issue.date] || []).push(issue);
		});

		if (currentView === 'agenda') {
			renderAgenda(issuesByDate, downloadingIssues, today, api_key);
			hide([CalendarEls.grid], [CalendarEls.agenda]);
			if (calendar.result.length || notInLibrary.length)
				hide([CalendarEls.empty]);
			else
				hide([], [CalendarEls.empty]);
		}
		else {
			renderGrid(range, issuesByDate, downloadingIssues, today);
			hide([CalendarEls.agenda, CalendarEls.empty], [CalendarEls.grid]);
		};
	};

	// Show the calendar right away, and add the pull list when it's loaded,
	// as fetching it from League of Comic Geeks can take a while.
	const noPullList = {result: {items: [], error: null}};
	Promise.all([
		fetchAPI('/calendar', api_key, params),
		fetchAPI('/activity/queue', api_key).catch(() => ({result: []}))
	])
	.then(([calendar, queue]) => {
		render(calendar, queue, noPullList);
		fetchAPI('/calendar/pull-list', api_key, {start: params.start, end: params.end})
		.then(pullList => {
			if (pullList.result.items.length || pullList.result.error)
				render(calendar, queue, pullList);
		})
		.catch(() => {});
	});
};

// code run on load

usingApiKey()
.then(api_key => {
	setView(view);
	CalendarEls.monitoredOnly.checked =
		getLocalStorage('calendar_monitored_only')['calendar_monitored_only'];

	CalendarEls.previous.onclick = e => { move(-1); loadCalendar(api_key); };
	CalendarEls.next.onclick = e => { move(1); loadCalendar(api_key); };
	CalendarEls.today.onclick = e => {
		anchorDate = startOfDay(new Date());
		loadCalendar(api_key);
	};
	CalendarEls.refresh.onclick = e => loadCalendar(api_key);
	CalendarEls.monitoredOnly.onchange = e => {
		setLocalStorage({'calendar_monitored_only': CalendarEls.monitoredOnly.checked});
		loadCalendar(api_key);
	};
	CalendarEls.viewButtons.forEach(b => b.onclick = e => {
		setView(b.dataset.view);
		if (b.dataset.view === 'agenda')
			anchorDate = startOfDay(new Date());
		loadCalendar(api_key);
	});

	document.addEventListener('keydown', e => {
		if (e.target.closest('input, textarea, select') || e.altKey || e.ctrlKey || e.metaKey)
			return;
		if (e.key === 'ArrowLeft') { move(-1); loadCalendar(api_key); }
		else if (e.key === 'ArrowRight') { move(1); loadCalendar(api_key); }
	});

	let lastView = effectiveView();
	window.addEventListener('resize', () => {
		if (effectiveView() !== lastView) {
			lastView = effectiveView();
			loadCalendar(api_key);
		};
	});

	loadCalendar(api_key);
});
