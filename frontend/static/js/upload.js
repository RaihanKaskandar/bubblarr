const UPLOAD_CHUNK_SIZE = 10 * 1024 * 1024;
const UPLOAD_CHUNK_ATTEMPTS = 5;
const SEARCH_DELAY = 250;

const uploadEls = {
	preBuildItem: document.querySelector('.pre-build-els .upload-item'),
	dropZone: document.getElementById('drop-zone'),
	fileInput: document.getElementById('file-input'),
	toolbar: document.getElementById('upload-toolbar'),
	selectAll: document.getElementById('select-all'),
	selectionCount: document.getElementById('selection-count'),
	importSelected: document.getElementById('import-selected'),
	clearFinished: document.getElementById('clear-finished'),
	list: document.getElementById('upload-list'),
	match: {
		search: document.getElementById('match-search'),
		searchInput: document.getElementById('match-search-input'),
		empty: document.getElementById('match-empty'),
		results: document.getElementById('match-results'),
		issues: document.getElementById('match-issues'),
		issueError: document.getElementById('match-issue-error'),
		volume: document.getElementById('match-volume'),
		from: document.getElementById('match-issue-from'),
		to: document.getElementById('match-issue-to'),
		applyAll: document.getElementById('match-apply-all'),
		save: document.getElementById('match-save')
	}
};

const confidenceLabels = {
	high: 'Recognised',
	low: 'Check this',
	manual: 'Your choice'
};

const reasonTexts = {
	comicvine_id: 'Exact match: the ComicVine ID in the file.',
	title: null,
	similar_title: 'The series name is only similar to this volume.',
	year: "The year of the file doesn't fit this volume.",
	volume_number: "The volume number of the file doesn't fit this volume.",
	special_version: "The type of the file (e.g. TPB) doesn't fit this volume.",
	annual: "The file doesn't look like the same kind of release as this volume (annual or not).",
	issue_not_found: "This volume doesn't seem to have this issue.",
	ambiguous: 'More than one volume in your library fits this file.',
	manual: null
};

const warningTexts = {
	unreadable: "This file doesn't seem to be a working comic archive. It might be damaged.",
	contains_issues: 'This archive contains multiple comics. Unpack it and upload the comics separately.'
};

// All files on the page. Keys are unique for each file on the page.
const items = new Map();
let nextItemKey = 0;
let uploading = false;
let importing = false;

//
// Rendering
//
function formatIssues(numbers) {
	if (!numbers.length)
		return 'No issue selected';
	if (numbers.length === 1)
		return `Issue ${numbers[0]}`;
	return `Issues ${numbers[0]} - ${numbers[numbers.length - 1]}`;
};

function formatVolume(match) {
	let result = match.volume_title;
	if (match.volume_number !== null && match.volume_number !== undefined)
		result += ` Volume ${match.volume_number}`;
	if (match.volume_year)
		result += ` (${match.volume_year})`;
	return result;
};

function describeDetected(detected) {
	const parts = [];
	if (detected.series)
		parts.push(detected.series);
	if (Array.isArray(detected.issue_number))
		parts.push(`#${detected.issue_number[0]} - ${detected.issue_number[1]}`);
	else if (detected.issue_number !== null)
		parts.push(`#${detected.issue_number}`);
	if (detected.year !== null)
		parts.push(`(${detected.year})`);

	const source = detected.source === 'comicinfo' ? 'metadata' : 'filename';
	return `From the ${source}: ${parts.join(' ') || 'nothing useful'}`;
};

function canImport(item) {
	return (
		item.state === 'ready'
		&& item.upload.match !== null
		&& item.upload.match.issue_ids.length > 0
	);
};

function setText(el, text) {
	el.textContent = text || '';
	el.classList.toggle('hidden', !text);
};

function renderItem(item) {
	const el = item.el;
	const upload = item.upload;
	const match = upload ? upload.match : null;
	el.dataset.state = item.state;

	el.querySelector('.item-progress').classList.toggle(
		'hidden', item.state !== 'uploading'
	);
	setText(el.querySelector('.item-status'), item.status);

	const ready = item.state === 'ready';
	const selectBox = el.querySelector('.item-select input');
	selectBox.disabled = !canImport(item);
	if (selectBox.disabled)
		selectBox.checked = false;
	el.querySelector('.item-select').classList.toggle(
		'hidden', item.state === 'imported'
	);
	el.querySelector('.item-remove').classList.toggle(
		'hidden', item.state === 'imported' || item.state === 'importing'
	);

	const matchEl = el.querySelector('.item-match');
	matchEl.classList.toggle('hidden', !ready);
	if (!ready) {
		delete el.dataset.confidence;
		hide([
			el.querySelector('.item-reason'),
			el.querySelector('.item-destination'),
			el.querySelector('.item-warnings')
		]);
		updateToolbar();
		return;
	}

	el.dataset.confidence = match ? match.confidence : 'none';
	el.querySelector('.item-badge').textContent = match
		? confidenceLabels[match.confidence]
		: 'Not recognised';

	const volumeLink = el.querySelector('.item-volume');
	const issues = el.querySelector('.item-issues');
	if (match) {
		volumeLink.textContent = formatVolume(match);
		volumeLink.href = `${url_base}/volumes/${match.volume_id}`;
		issues.textContent = formatIssues(match.issue_numbers);
	} else {
		volumeLink.textContent = '';
		volumeLink.removeAttribute('href');
		issues.textContent = '';
	}

	const reasons = [describeDetected(upload.detected)];
	if (match && reasonTexts[match.reason])
		reasons.unshift(reasonTexts[match.reason]);
	if (!match)
		reasons.unshift('No volume in your library fits this file. Choose one, or add the volume and reload this page.');
	setText(el.querySelector('.item-reason'), reasons.join(' '));

	setText(
		el.querySelector('.item-destination'),
		match ? match.destination : ''
	);

	const warnings = upload.warnings.map(w => warningTexts[w] || w);
	if (match && match.existing_files.length)
		warnings.push(
			`Already in your library: ${match.existing_files.join(', ')}. `
			+ 'Importing keeps that file and adds this one next to it.'
		);
	if (item.error)
		warnings.push(item.error);

	const warningsEl = el.querySelector('.item-warnings');
	warningsEl.innerHTML = '';
	warnings.forEach(w => {
		const entry = document.createElement('li');
		entry.textContent = w;
		warningsEl.appendChild(entry);
	});
	warningsEl.classList.toggle('hidden', !warnings.length);

	updateToolbar();
};

function selectedItems() {
	return [...items.values()].filter(
		i => i.state === 'ready'
			&& i.el.querySelector('.item-select input').checked
	);
};

function updateToolbar() {
	const importable = [...items.values()].filter(canImport);
	const selected = selectedItems();

	uploadEls.toolbar.classList.toggle('hidden', items.size === 0);
	uploadEls.selectionCount.textContent = `${selected.length} of ${importable.length} selected`;
	uploadEls.selectAll.checked = (
		importable.length > 0 && selected.length === importable.length
	);
	uploadEls.selectAll.disabled = importable.length === 0;

	uploadEls.importSelected.disabled = importing || selected.length === 0;
	uploadEls.importSelected.textContent = importing
		? 'Importing...'
		: `Import ${selected.length || ''}`.trim();

	uploadEls.clearFinished.classList.toggle(
		'hidden',
		![...items.values()].some(i => i.state === 'imported')
	);
};

function isSafeMatch(upload) {
	return (
		upload.match !== null
		&& upload.match.confidence === 'high'
		&& upload.match.issue_ids.length > 0
		&& upload.match.existing_files.length === 0
		&& upload.warnings.length === 0
	);
};

function setItemReady(item, upload) {
	item.upload = upload;
	item.state = 'ready';
	item.status = '';
	item.error = '';
	renderItem(item);
	item.el.querySelector('.item-select input').checked = isSafeMatch(upload);
	updateToolbar();
};

function createItem(state, status='') {
	const el = uploadEls.preBuildItem.cloneNode(true);
	const item = {
		key: nextItemKey++,
		el: el,
		state: state,
		status: status,
		error: '',
		file: null,
		id: null,
		upload: null,
		xhr: null,
		cancelled: false
	};

	el.querySelector('.item-select input').onchange = updateToolbar;
	el.querySelector('.item-change').onclick = () => openMatchWindow(item);
	el.querySelector('.item-remove').onclick = () => removeItem(item);

	items.set(item.key, item);
	uploadEls.list.appendChild(el);
	return item;
};

function removeItem(item) {
	items.delete(item.key);
	item.el.remove();
	updateToolbar();

	item.cancelled = true;
	if (item.xhr)
		item.xhr.abort();

	if (item.id !== null)
		usingApiKey().then(apiKey =>
			sendAPI('DELETE', `/uploads/${item.id}`, apiKey).catch(() => {})
		);
};

//
// Uploading
//
async function describeError(error) {
	if (error && typeof error.json === 'function') {
		const json = await error.json().catch(() => null);
		if (json) {
			if (json.error === 'UploadNoSpace')
				return `Not enough free disk space on the server (${convertSize(json.result.available, 1)} free).`;
			if (
				json.error === 'InvalidKeyValue'
				&& json.result.key === 'filename'
			)
				return 'This type of file is not supported.';
			if (
				json.error === 'InvalidKeyValue'
				&& json.result.key === 'size'
			)
				return 'This file is empty.';
			if (json.error === 'UploadNotFound')
				return 'The upload no longer exists on the server. Upload the file again.';
		}
	}

	const status = error ? error.status : 0;
	if (status === 413)
		return 'The server or a proxy in front of it refused the file because it is too large.';
	if (status)
		return `The server responded with an error (${status}).`;
	return 'Could not reach the server.';
};

function sendChunk(item, apiKey, offset, chunk) {
	return new Promise((resolve, reject) => {
		const xhr = new XMLHttpRequest();
		item.xhr = xhr;
		xhr.open(
			'PUT',
			`${url_base}/api/uploads/${item.id}?api_key=${apiKey}&offset=${offset}`
		);
		xhr.setRequestHeader('Content-Type', 'application/octet-stream');
		xhr.responseType = 'json';

		xhr.upload.onprogress = e => {
			if (e.lengthComputable)
				setProgress(item, offset + e.loaded);
		};
		xhr.onload = () => {
			item.xhr = null;
			if (xhr.status === 200)
				resolve(xhr.response.result.received);

			// The server has a different view on how far we are. Go along.
			else if (xhr.status === 409)
				resolve(xhr.response.result.received);

			else if (xhr.status === 401) {
				setLocalStorage({api_key: null});
				window.location.href = `${url_base}/login?redirect=${window.location.pathname}`;
			}

			else
				reject({status: xhr.status});
		};
		xhr.onerror = xhr.onabort = xhr.ontimeout = () => {
			item.xhr = null;
			reject({status: 0});
		};

		xhr.send(chunk);
	});
};

async function sendChunkWithRetries(item, apiKey, offset, chunk) {
	for (let attempt = 1; ; attempt++) {
		try {
			return await sendChunk(item, apiKey, offset, chunk);

		} catch (error) {
			const canRetry = error.status === 0 || error.status >= 500;
			if (item.cancelled || !canRetry || attempt >= UPLOAD_CHUNK_ATTEMPTS)
				throw error;

			item.status = `Connection problem, retrying (${attempt}/${UPLOAD_CHUNK_ATTEMPTS - 1})...`;
			renderItem(item);
			await new Promise(r => setTimeout(r, attempt * 1000));
			item.status = '';
			renderItem(item);
		}
	}
};

function setProgress(item, sent) {
	item.el.querySelector('progress').value = sent / item.file.size * 100;
};

async function uploadItem(item, apiKey) {
	item.state = 'uploading';
	item.status = '';
	renderItem(item);

	const created = await sendAPI('POST', '/uploads', apiKey, {}, {
		filename: item.file.name,
		size: item.file.size
	}).then(r => r.json());
	item.id = created.result.id;

	if (item.cancelled) {
		// Removed before the server had told us the ID
		await sendAPI('DELETE', `/uploads/${item.id}`, apiKey).catch(() => {});
		return;
	}

	let offset = 0;
	while (offset < item.file.size) {
		if (item.cancelled)
			return;

		offset = await sendChunkWithRetries(
			item,
			apiKey,
			offset,
			item.file.slice(offset, offset + UPLOAD_CHUNK_SIZE)
		);
		setProgress(item, offset);
	}

	item.state = 'analysing';
	item.status = 'Recognising...';
	renderItem(item);

	const completed = await sendAPI(
		'POST', `/uploads/${item.id}/complete`, apiKey
	).then(r => r.json());

	if (!item.cancelled)
		setItemReady(item, completed.result);
};

async function processQueue(apiKey) {
	if (uploading)
		return;
	uploading = true;

	for (;;) {
		const item = [...items.values()].find(i => i.state === 'queued');
		if (!item)
			break;

		try {
			await uploadItem(item, apiKey);

		} catch (error) {
			if (item.cancelled)
				continue;

			item.state = 'failed';
			item.status = await describeError(error);
			renderItem(item);

			// Don't leave the incomplete file behind on the server
			if (item.id !== null) {
				sendAPI('DELETE', `/uploads/${item.id}`, apiKey).catch(() => {});
				item.id = null;
			}
		}
	}

	uploading = false;
};

function addFiles(files, apiKey) {
	[...files].forEach(file => {
		const item = createItem('queued', 'Waiting...');
		item.file = file;
		item.el.querySelector('.item-name').textContent = file.name;
		item.el.querySelector('.item-size').textContent = convertSize(file.size, 1);
		renderItem(item);
	});
	processQueue(apiKey);
};

function addExistingUpload(upload) {
	const item = createItem('ready');
	item.id = upload.id;
	item.el.querySelector('.item-name').textContent = upload.filename;
	item.el.querySelector('.item-size').textContent = convertSize(upload.size, 1);
	setItemReady(item, upload);
};

//
// Importing
//
async function importItem(item, apiKey) {
	const match = item.upload.match;

	item.state = 'importing';
	item.status = 'Importing...';
	item.error = '';
	renderItem(item);

	try {
		const response = await sendAPI(
			'POST', `/uploads/${item.id}/import`, apiKey, {},
			{volume_id: match.volume_id, issue_ids: match.issue_ids}
		).then(r => r.json());

		item.id = null;
		item.state = 'imported';
		item.status = `Imported to ${response.result.filepath}`;
		renderItem(item);

	} catch (error) {
		const description = await describeError(error);
		item.state = 'ready';
		item.status = '';
		item.error = `Importing failed: ${description}`;
		renderItem(item);
	}
};

async function importSelected(apiKey) {
	const selected = selectedItems();
	importing = true;
	updateToolbar();

	for (const item of selected)
		await importItem(item, apiKey);

	importing = false;
	updateToolbar();
};

//
// Changing the match
//
const matchState = {
	item: null,
	volumeId: null,
	issues: [],
	searchTimer: null
};

function issueOption(issue) {
	const option = document.createElement('option');
	option.value = issue.id;
	option.textContent = issue.title
		? `${issue.issue_number} - ${issue.title}`
		: issue.issue_number;
	return option;
};

function fillIssueTo() {
	const m = uploadEls.match;
	const previous = m.to.value;
	const start = matchState.issues.findIndex(
		i => String(i.id) === m.from.value
	);

	m.to.innerHTML = '';
	const single = document.createElement('option');
	single.value = '';
	single.textContent = 'Only this issue';
	m.to.appendChild(single);

	matchState.issues.slice(start + 1).forEach(
		issue => m.to.appendChild(issueOption(issue))
	);
	m.to.value = previous;
	if (m.to.selectedIndex === -1)
		m.to.value = '';
};

function otherFilesOfSeries(item) {
	const series = (item.upload.detected.series || '').toLowerCase();
	return [...items.values()].filter(i =>
		i !== item
		&& i.state === 'ready'
		&& (i.upload.detected.series || '').toLowerCase() === series
	);
};

function chosenIssueIds() {
	const m = uploadEls.match;
	const start = matchState.issues.findIndex(
		i => String(i.id) === m.from.value
	);
	const end = m.to.value === ''
		? start
		: matchState.issues.findIndex(i => String(i.id) === m.to.value);

	return matchState.issues.slice(start, end + 1).map(i => i.id);
};

async function selectVolume(volume, apiKey) {
	const m = uploadEls.match;
	const item = matchState.item;
	matchState.volumeId = volume.id;
	m.save.disabled = true;

	m.results.querySelectorAll('button').forEach(b => b.setAttribute(
		'aria-pressed', String(b.dataset.volumeId === String(volume.id))
	));

	const [volumeData, match] = await Promise.all([
		fetchAPI(`/volumes/${volume.id}`, apiKey),
		sendAPI(
			'POST', `/uploads/${item.id}/match`, apiKey, {},
			{volume_id: volume.id}
		).then(r => r.json())
	]);

	if (matchState.item !== item || matchState.volumeId !== volume.id)
		return;

	matchState.issues = volumeData.result.issues.sort(
		(a, b) => a.calculated_issue_number - b.calculated_issue_number
	);

	m.volume.textContent = formatVolume(match.result);
	m.from.innerHTML = '';
	matchState.issues.forEach(issue => m.from.appendChild(issueOption(issue)));

	const ids = match.result.issue_ids;
	m.from.value = ids.length ? ids[0] : '';
	if (m.from.selectedIndex === -1)
		m.from.selectedIndex = 0;
	m.to.value = '';
	fillIssueTo();
	if (ids.length > 1)
		m.to.value = ids[ids.length - 1];

	const others = otherFilesOfSeries(item);
	m.applyAll.querySelector('input').checked = false;
	m.applyAll.querySelector('span').textContent =
		`Also use this volume for the ${others.length} other file(s) of this series`;
	m.applyAll.classList.toggle('hidden', !others.length);

	const hasIssues = matchState.issues.length > 0;
	m.issueError.classList.toggle('hidden', hasIssues);
	document.querySelector('.match-issue-selects').classList.toggle(
		'hidden', !hasIssues
	);
	m.save.disabled = !hasIssues;
	hide([], [m.issues]);
};

function searchVolumes(apiKey) {
	const m = uploadEls.match;
	const query = m.searchInput.value.trim();
	if (!query) {
		m.results.innerHTML = '';
		hide([m.empty]);
		return;
	}

	fetchAPI('/volumes', apiKey, {query: encodeURIComponent(query)})
	.then(json => {
		m.results.innerHTML = '';
		json.result.slice(0, 50).forEach(volume => {
			const button = document.createElement('button');
			button.type = 'button';
			button.dataset.volumeId = volume.id;
			button.textContent = formatVolume({
				volume_title: volume.title,
				volume_number: volume.volume_number,
				volume_year: volume.year
			});
			button.setAttribute(
				'aria-pressed', String(volume.id === matchState.volumeId)
			);
			button.onclick = () => selectVolume(volume, apiKey);

			const entry = document.createElement('li');
			entry.appendChild(button);
			m.results.appendChild(entry);
		});
		m.empty.classList.toggle('hidden', json.result.length > 0);
	});
};

function openMatchWindow(item) {
	const m = uploadEls.match;
	const match = item.upload.match;

	matchState.item = item;
	matchState.volumeId = null;
	matchState.issues = [];
	m.save.disabled = true;
	m.results.innerHTML = '';
	hide([m.issues, m.empty]);

	m.searchInput.value = match
		? match.volume_title
		: (item.upload.detected.series || '');
	showWindow('match-window');

	usingApiKey().then(apiKey => {
		searchVolumes(apiKey);
		if (match)
			selectVolume({id: match.volume_id}, apiKey);
	});
};

async function saveMatch(apiKey) {
	const m = uploadEls.match;
	const item = matchState.item;
	const volumeId = matchState.volumeId;
	const issueIds = chosenIssueIds();
	const applyToOthers = (
		!m.applyAll.classList.contains('hidden')
		&& m.applyAll.querySelector('input').checked
	);
	const others = applyToOthers ? otherFilesOfSeries(item) : [];

	m.save.disabled = true;
	closeWindow();

	for (const [target, body] of [
		[item, {volume_id: volumeId, issue_ids: issueIds}],
		...others.map(other => [other, {volume_id: volumeId}])
	]) {
		try {
			const response = await sendAPI(
				'POST', `/uploads/${target.id}/match`, apiKey, {}, body
			).then(r => r.json());

			target.upload.match = response.result;
			target.error = '';
			renderItem(target);
			target.el.querySelector('.item-select input').checked = canImport(target);

		} catch (error) {
			target.error = `Changing the match failed: ${await describeError(error)}`;
			renderItem(target);
		}
	}
	updateToolbar();
};

//
// Code run on load
//
usingApiKey()
.then(apiKey => {
	const m = uploadEls.match;

	fetchAPI('/uploads', apiKey)
	.then(json => json.result.forEach(addExistingUpload));

	uploadEls.fileInput.onchange = e => {
		addFiles(e.target.files, apiKey);
		e.target.value = '';
	};

	['dragenter', 'dragover'].forEach(name =>
		uploadEls.dropZone.addEventListener(name, e => {
			e.preventDefault();
			uploadEls.dropZone.classList.add('drag-over');
		})
	);
	['dragleave', 'drop'].forEach(name =>
		uploadEls.dropZone.addEventListener(name, e => {
			e.preventDefault();
			uploadEls.dropZone.classList.remove('drag-over');
		})
	);
	uploadEls.dropZone.addEventListener(
		'drop',
		e => addFiles(e.dataTransfer.files, apiKey)
	);

	uploadEls.importSelected.onclick = () => importSelected(apiKey);

	m.search.onsubmit = e => {
		e.preventDefault();
		searchVolumes(apiKey);
	};
	m.searchInput.oninput = () => {
		clearTimeout(matchState.searchTimer);
		matchState.searchTimer = setTimeout(
			() => searchVolumes(apiKey), SEARCH_DELAY
		);
	};
	m.from.onchange = fillIssueTo;
	m.save.onclick = () => saveMatch(apiKey);
});

uploadEls.selectAll.onchange = () => {
	[...items.values()].filter(canImport).forEach(item =>
		item.el.querySelector('.item-select input').checked = uploadEls.selectAll.checked
	);
	updateToolbar();
};

uploadEls.clearFinished.onclick = () => {
	[...items.values()]
		.filter(i => i.state === 'imported')
		.forEach(removeItem);
};

// Stop a dropped file from replacing the page if it misses the drop zone
['dragover', 'drop'].forEach(name =>
	window.addEventListener(name, e => e.preventDefault())
);

window.addEventListener('beforeunload', e => {
	const busy = [...items.values()].some(
		i => ['queued', 'uploading', 'analysing'].includes(i.state)
	);
	if (busy || importing)
		e.preventDefault();
});
