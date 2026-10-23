const brokenClientReasonMap = {
    connection_error: "Failed to connect",
    not_client_instance: "What was connected to was not the expected client",
    version_not_supported: "The version is not supported",
    failed_processing_response: "Got an unexpected response back",
    access_denied: "Access denied by client but not because of invalid credentials",
	invalid_credentials: "Failed to login with the given credentials"
}

function createGCAvoidLargeDownloadsInput(inputId) {
	const row = document.createElement('tr');
	const header = document.createElement('th');
	const label = document.createElement('label');
	label.innerText = 'Avoid Large Downloads';
	label.setAttribute('for', inputId);
	header.appendChild(label);
	row.appendChild(header)
	const container = document.createElement('td');
	const input = document.createElement('input');
	input.type = 'checkbox'
	input.id = inputId;
	container.appendChild(input);
	const description = document.createElement('p');
	description.innerText = "If a download on GetComics is larger than 400MB, then avoid downloading the file directly from the GetComics servers. Try all external services first. GetComics throttles the download speed after downloading 400MB of a file."
	container.appendChild(description)
	row.appendChild(container);
	return row;
}

const defaultServicePreference = [
	"Mega", "MediaFire",
	"WeTransfer", "Pixeldrain",
	"GetComics", "GetComics (torrent)"
]

function updatePrefOrder(e) {
	const otherSelects = document.querySelectorAll(
		`#pref-table select:not([data-place="${e.target.dataset.place}"])`
	);

	// Find select that has the value of the target select
	otherSelects.forEach(select => {
		if (select.value !== e.target.value)
			return;

		// Set it to old value of target select
		const usedValues = new Set([...document.querySelectorAll("#pref-table select")].map(s => s.value));
		const openValue = defaultServicePreference.filter(e => !usedValues.has(e))[0];
		select.value = openValue;
	});
}

function createGCServicePreferenceInput(inputId) {
	const row = document.createElement('tr');
	const header = document.createElement('th');
	const label = document.createElement('label');
	label.innerText = 'Service Preference';
	label.setAttribute('for', 'pref-1');
	header.appendChild(label);
	row.appendChild(header)

	const container = document.createElement('td');

	const prefTable = document.createElement("table");
	prefTable.id = "pref-table";
	prefTable.classList.add("short-table");
	const prefTableBody = document.createElement("tbody");
	prefTableBody.id = inputId;
	prefTable.appendChild(prefTableBody);

	defaultServicePreference.forEach((pref, idx) => {
		const prefEntry = document.createElement("tr");
		const prefHeader = document.createElement("th");
		prefHeader.innerText = idx + 1;
		prefEntry.appendChild(prefHeader);
		const prefContent = document.createElement("td");
		const prefSelect = document.createElement("select");
		prefSelect.id = `pref-${idx+1}`;
		prefSelect.dataset.place = idx + 1;
		prefSelect.onchange = updatePrefOrder;
		defaultServicePreference.forEach(serviceOption => {
			const option = document.createElement("option");
			option.value = serviceOption;
			option.innerText = serviceOption.charAt(0).toUpperCase() + serviceOption.slice(1);
			if (serviceOption === pref)
				option.selected = true;
			prefSelect.appendChild(option);
		});
		prefContent.appendChild(prefSelect);
		prefEntry.appendChild(prefContent);
		prefTableBody.appendChild(prefEntry);
	});
	container.appendChild(prefTable);
	
	const description = document.createElement('p');
	description.innerText = "The preference for services to download from when using GetComics as the source."
	container.appendChild(description)

	row.appendChild(container);
	return row;
}

function createInputRow(inputId, labelText, type, description, attributes={}) {
	const row = document.createElement('tr');
	const header = document.createElement('th');
	const label = document.createElement('label');
	label.innerText = labelText;
	label.setAttribute('for', inputId);
	header.appendChild(label);
	row.appendChild(header);

	const container = document.createElement('td');
	const input = document.createElement('input');
	input.type = type;
	input.id = inputId;
	Object.entries(attributes).forEach(([k, v]) => input.setAttribute(k, v));
	container.appendChild(input);

	if (description) {
		const desc = document.createElement('p');
		desc.innerText = description;
		container.appendChild(desc);
	};

	row.appendChild(container);
	return row;
};

// Torznab fields, in the order they're shown in the form
const extraFields = {
	api_key: prefix => createInputRow(
		`${prefix}-api-key-input`, 'API Key', 'text',
		'The API key of Jackett or Prowlarr.',
		{autocomplete: 'off'}
	),
	categories: prefix => createInputRow(
		`${prefix}-categories-input`, 'Categories', 'text',
		'Comma separated list of the categories to search in. 7030 is Books/Comics.',
		{placeholder: '7030'}
	),
	min_seeders: prefix => createInputRow(
		`${prefix}-min-seeders-input`, 'Minimum Seeders', 'number',
		'Ignore releases with less seeders than this.',
		{min: 0, step: 1}
	),
	priority: prefix => createInputRow(
		`${prefix}-priority-input`, 'Indexer Priority', 'number',
		'From 1 (highest) to 50 (lowest). When releases are equally good, the one from the indexer with the highest priority is preferred. Default is 25.',
		{min: 1, max: 50, step: 1}
	)
};

function addExtraFields(form, prefix, requiredTokens) {
	Object.entries(extraFields).forEach(([token, createRow]) => {
		if (requiredTokens.includes(token))
			form.appendChild(createRow(prefix));
	});

	if (requiredTokens.includes('gc_avoid_large_downloads'))
		form.appendChild(createGCAvoidLargeDownloadsInput(`${prefix}-gc-avoid-input`));

	if (requiredTokens.includes('gc_service_preference'))
		form.appendChild(createGCServicePreferenceInput(`${prefix}-gc-service-preference-input`));
};

function fillExtraFields(form, prefix, data) {
	const set = (selector, value) => {
		const el = form.querySelector(selector);
		if (el) el.value = value ?? '';
	};
	set(`#${prefix}-api-key-input`, data.api_key);
	set(`#${prefix}-categories-input`, (data.categories || []).join(','));
	set(`#${prefix}-min-seeders-input`, data.min_seeders);
	set(`#${prefix}-priority-input`, data.priority);

	const gcAvoidInput = form.querySelector(`#${prefix}-gc-avoid-input`);
	if (gcAvoidInput)
		gcAvoidInput.checked = data.gc_avoid_large_downloads ?? true;

	if (data.gc_service_preference)
		form.querySelectorAll('#pref-table select').forEach((pref, idx) => {
			pref.value = data.gc_service_preference[idx];
		});
};

function collectFormData(form, prefix) {
	const value = selector => form.querySelector(selector)?.value;
	const intValue = selector => {
		const v = value(selector);
		return (v === undefined || v === '') ? null : parseInt(v);
	};

	const prefTable = form.querySelectorAll('#pref-table select');
	const categories = value(`#${prefix}-categories-input`);

	return {
		title: value(`#${prefix}-title-input`),
		enabled: form.querySelector(`#${prefix}-enabled-input`).checked,
		url: value(`#${prefix}-url-input`),
		api_key: value(`#${prefix}-api-key-input`) ?? null,
		categories: categories
			? categories.split(',').map(c => c.trim()).filter(c => c)
			: null,
		min_seeders: intValue(`#${prefix}-min-seeders-input`),
		priority: intValue(`#${prefix}-priority-input`),
		gc_service_preference: prefTable.length
			? [...prefTable].map(e => e.value)
			: null,
		gc_avoid_large_downloads:
			form.querySelector(`#${prefix}-gc-avoid-input`)?.checked ?? null
	};
};

function removeExtraFields(form, prefix) {
	form.querySelectorAll(
		`tr:not(:has(input#${prefix}-title-input, input#${prefix}-enabled-input, input#${prefix}-url-input))`
	).forEach(el => el.remove());
};

async function testIndexer(apiKey, prefix) {
	const error = document.querySelector(`#${prefix}-error`);
	hide([error]);
	const form = document.querySelector(`#${prefix}-indexer-form tbody`);
	const testButton = document.querySelector(`#test-indexer-${prefix}`);
	testButton.classList.remove('show-success', 'show-fail');

	const data = {
		...collectFormData(form, prefix),
		download_type: parseInt(form.dataset.download_type),
		client_type: form.dataset.type
	};

	return await sendAPI('POST', '/indexers/test', apiKey, {}, data)
	.then(response => response.json())
	.then(json => {
		if (json.result.success)
			// Test successful
			testButton.classList.add('show-success');
		else {
			// Test failed
			testButton.classList.add('show-fail');
			error.innerText = brokenClientReasonMap[json.result.description];
			hide([], [error]);
		};
		return json.result.success;
	})
	.catch(e => {
		testButton.classList.add('show-fail');
		error.innerText = 'One of the values is invalid';
		hide([], [error]);
		return false;
	});
};

function loadEditIndexer(apiKey, indexerId) {
	const form = document.querySelector('#edit-indexer-form tbody');
	form.dataset.id = indexerId;
	removeExtraFields(form, 'edit');
	document.querySelector('#test-indexer-edit').classList.remove(
		'show-success', 'show-fail'
	)
	hide([document.querySelector('#edit-error')]);

	fetchAPI(`/indexers/${indexerId}`, apiKey)
	.then(clientData => {
		const indexer = clientData.result;
		form.dataset.download_type = indexer.download_type;
		form.dataset.type = indexer.client_type;

		form.querySelector('#edit-title-input').value = indexer.title || '';
		form.querySelector('#edit-enabled-input').checked = indexer.enabled;
		form.querySelector('#edit-url-input').value = indexer.url;

		addExtraFields(form, 'edit', indexer.required_tokens);
		fillExtraFields(form, 'edit', indexer);

		hide([], [document.querySelector('#edit-prowlarr-note')]);
		if (!indexer.prowlarr_synced)
			hide([document.querySelector('#edit-prowlarr-note')]);

		showWindow('edit-indexer-window');
	});
};

function saveEditIndexer() {
	usingApiKey()
	.then(apiKey => {
		testIndexer(apiKey, 'edit').then(result => {
			if (!result)
				return;

			const form = document.querySelector('#edit-indexer-form tbody');
			const indexerId = form.dataset.id;

			sendAPI('PUT', `/indexers/${indexerId}`, apiKey, {}, collectFormData(form, 'edit'))
			.then(response => {
				loadIndexers(apiKey);
				closeWindow();
			});
		});
	});
};

function deleteIndexer(apiKey) {
	const indexerId = document.querySelector('#edit-indexer-form tbody').dataset.id;
	sendAPI('DELETE', `/indexers/${indexerId}`, apiKey)
	.then(response => {
		loadIndexers(apiKey);
		closeWindow();
	});
};

function loadIndexerOptionList(apiKey, downloadType) {
	const table = document.querySelector('#choose-indexer-list');
	table.innerHTML = '';

	fetchAPI('/indexers/options', apiKey)
	.then(json => {
		Object.keys(json.result[downloadType]).forEach(clientType => {
			const entry = document.createElement('button');
			entry.innerText = clientType;
			entry.onclick = e => loadAddIndexer(apiKey, downloadType, clientType);
			table.appendChild(entry);
		});
		showWindow('choose-indexer-window');
	});
};

const addDefaults = {
	categories: [7030],
	min_seeders: 1,
	priority: 25,
	gc_avoid_large_downloads: false
};

function loadAddIndexer(apiKey, downloadType, clientType) {
	const error = document.querySelector("#add-error");
	hide([error]);

	const form = document.querySelector('#add-indexer-form tbody');
	form.dataset.download_type = downloadType;
	form.dataset.type = clientType;
	removeExtraFields(form, 'add');
	document.querySelector('#test-indexer-add').classList.remove(
		'show-success', 'show-fail'
	)
	form.querySelectorAll(
		'#add-title-input, #add-url-input'
	).forEach(el => el.value = '');
	form.querySelector('#add-enabled-input').checked = true;

	fetchAPI('/indexers/options', apiKey)
	.then(json => {
		const clientOptions = json.result[downloadType][clientType];
		addExtraFields(form, 'add', clientOptions.required_tokens);
		fillExtraFields(form, 'add', addDefaults);
		showWindow('add-indexer-window');
	});
};

function saveAddIndexer() {
	const error = document.querySelector("#add-error");
	hide([error]);

	usingApiKey()
	.then(apiKey => {
		testIndexer(apiKey, 'add').then(result => {
			if (!result)
				return;

			const form = document.querySelector('#add-indexer-form tbody');
			const data = {
				...collectFormData(form, 'add'),
				download_type: parseInt(form.dataset.download_type),
				client_type: form.dataset.type
			};
			sendAPI('POST', '/indexers', apiKey, {}, data)
			.then(response => {
				loadIndexers(apiKey);
				closeWindow();
			})
			.catch(e => {
				if (e.status === 400) {
					// Only one instance allowed
					error.innerText = "Only one instance of this indexer allowed";
					hide([], [error]);
				}
				else
					console.log(e);
			});
		});
	});
};

const typeToList = {
	1: document.querySelector("#ddl-indexer-list"),
	2: document.querySelector("#torrent-indexer-list")
};

function loadIndexers(apiKey) {
	Object.values(typeToList).forEach(
		l => l.querySelectorAll(":not(:first-child)").forEach(el => el.remove())
	);

	fetchAPI('/indexers', apiKey)
	.then(json => {
		json.result.forEach(indexer => {
			const entry = document.createElement('button');
			entry.onclick = e => loadEditIndexer(apiKey, indexer.id);
			entry.innerText = indexer.title;
			if (!indexer.enabled)
				entry.classList.add('disabled-indexer');
			if (indexer.prowlarr_synced) {
				const badge = document.createElement('span');
				badge.classList.add('indexer-badge');
				badge.innerText = 'Prowlarr';
				entry.appendChild(badge);
			};
			typeToList[indexer.download_type].appendChild(entry);
		});
	});
};

// code run on load

usingApiKey()
.then(api_key => {
	loadIndexers(api_key);
	document.querySelector('#delete-indexer-edit').onclick = e => deleteIndexer(api_key);
	document.querySelector('#test-indexer-edit').onclick = e => testIndexer(api_key, 'edit');
	document.querySelector('#test-indexer-add').onclick = e => testIndexer(api_key, 'add');

	Object.entries(typeToList).forEach(tl => {
		tl[1].querySelector('button:first-child').onclick = e => loadIndexerOptionList(api_key, tl[0]);
	})
});

document.querySelector('#edit-indexer-form').action = 'javascript:saveEditIndexer()';
document.querySelector('#add-indexer-form').action = 'javascript:saveAddIndexer()';
