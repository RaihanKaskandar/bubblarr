const MetadataEls = {
	dateType: document.querySelector('#date-type-input'),
	comicgeeks: {
		username: document.querySelector('#comicgeeks-username-input'),
		password: document.querySelector('#comicgeeks-password-input'),
		collection: document.querySelector('#comicgeeks-collection-input'),
		pullList: document.querySelector('#comicgeeks-pull-list-input'),
		test: document.querySelector('#comicgeeks-test-button'),
		sync: document.querySelector('#comicgeeks-sync-button'),
		status: document.querySelector('#comicgeeks-status')
	}
};

const comicgeeksErrors = {
	invalid_credentials: 'Wrong username or password',
	connection_error: "Couldn't connect to League of Comic Geeks",
	failed_processing_response: 'League of Comic Geeks responded in an unexpected way (their website might have changed)'
};

function setComicgeeksStatus(text, state=null) {
	const status = MetadataEls.comicgeeks.status;
	status.innerText = text;
	status.classList.remove('success', 'error');
	if (state)
		status.classList.add(state);
};

function fillSettings(api_key) {
	fetchAPI('/settings', api_key)
	.then(json => {
		MetadataEls.dateType.value = json.result.date_type;
		MetadataEls.comicgeeks.username.value = json.result.comicgeeks_username;
		MetadataEls.comicgeeks.password.value = json.result.comicgeeks_password;
		MetadataEls.comicgeeks.collection.checked = json.result.comicgeeks_sync_collection;
		MetadataEls.comicgeeks.pullList.checked = json.result.comicgeeks_pull_list;
	});
};

function getFormData() {
	return {
		'date_type': MetadataEls.dateType.value,
		'comicgeeks_username': MetadataEls.comicgeeks.username.value.trim(),
		'comicgeeks_password': MetadataEls.comicgeeks.password.value,
		'comicgeeks_sync_collection': MetadataEls.comicgeeks.collection.checked,
		'comicgeeks_pull_list': MetadataEls.comicgeeks.pullList.checked
	};
};

async function saveSettings(api_key) {
	document.querySelector("#save-button p").innerText = 'Saving';
	return sendAPI('PUT', '/settings', api_key, {}, getFormData())
	.then(response => response.json())
	.then(json => {
		document.querySelector("#save-button p").innerText = 'Saved';
		return true;
	})
	.catch(e => {
		document.querySelector("#save-button p").innerText = 'Failed';
		console.log(e);
		return false;
	});
};

function testComicgeeks(api_key) {
	const data = getFormData();
	if (!data.comicgeeks_username || !data.comicgeeks_password) {
		setComicgeeksStatus('Enter a username and password first', 'error');
		return;
	};

	setComicgeeksStatus('Testing...');
	sendAPI('POST', '/comicgeeks/test', api_key, {}, {
		username: data.comicgeeks_username,
		password: data.comicgeeks_password
	})
	.then(response => response.json())
	.then(json => {
		if (json.result.success)
			setComicgeeksStatus('Logged in successfully', 'success');
		else
			setComicgeeksStatus(
				comicgeeksErrors[json.result.description] || 'Failed to log in',
				'error'
			);
	})
	.catch(e => setComicgeeksStatus('Failed to log in', 'error'));
};

function syncComicgeeks(api_key) {
	const data = getFormData();
	if (!data.comicgeeks_username || !data.comicgeeks_password) {
		setComicgeeksStatus('Enter a username and password first', 'error');
		return;
	};

	// The sync uses the saved settings
	saveSettings(api_key)
	.then(saved => {
		if (!saved)
			return;
		sendAPI('POST', '/system/tasks', api_key, {}, {cmd: 'comicgeeks_sync'})
		.then(response => setComicgeeksStatus(
			'Sync started, see the task list for its progress', 'success'
		))
		.catch(e => setComicgeeksStatus('Failed to start sync', 'error'));
	});
};

// code run on load

usingApiKey()
.then(api_key => {
	fillSettings(api_key);
	document.querySelector('#save-button').onclick = e => saveSettings(api_key);
	MetadataEls.comicgeeks.test.onclick = e => testComicgeeks(api_key);
	MetadataEls.comicgeeks.sync.onclick = e => syncComicgeeks(api_key);
});
