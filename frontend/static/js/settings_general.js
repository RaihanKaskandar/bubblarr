function fillSettings(api_key) {
	fetchAPI('/settings', api_key)
	.then(json => {
		document.querySelector('#bind-address-input').value = json.result.host;
		document.querySelector('#port-input').value = json.result.port;
		document.querySelector('#url-base-input').value = json.result.url_base;
		document.querySelector('#username-input').value = json.result.auth_username;
		document.querySelector('#password-input').value = json.result.auth_password;
		document.querySelector('#api-input').value = api_key;
		document.querySelector('#proxy-type-input').value = json.result.proxy_type || '';
		document.querySelector('#proxy-host-input').value = json.result.proxy_host;
		document.querySelector('#proxy-port-input').value = json.result.proxy_port;
		document.querySelector('#proxy-username-input').value = json.result.proxy_username;
		document.querySelector('#proxy-password-input').value = json.result.proxy_password;
		document.querySelector('#proxy-ignored-addresses-input').value = json.result.proxy_ignored_addresses.join(',');
		document.querySelector('#cv-input').value = json.result.comicvine_api_key;
		document.querySelector('#flaresolverr-input').value = json.result.flaresolverr_base_url;
		document.querySelector('#log-level-input').value = json.result.log_level;
		document.querySelector('#db-backup-folder-input').value = json.result.db_backup_folder;
		document.querySelector('#db-backup-count-input').value = json.result.db_backup_amount;
		document.querySelector('#webhook-urls-input').value = json.result.webhook_urls.join('\n');
		document.querySelector('#webhook-secret-input').value = json.result.webhook_secret;
		
		if (json.result.auth_username && json.result.auth_password) {
			document.querySelector('#auth-toggle').value = 'username-password';
		} else if (json.result.auth_password) {
			document.querySelector('#auth-toggle').value = 'password';
		};
	});
	document.querySelector('#theme-input').value = getLocalStorage('theme')['theme'];
};

function saveSettings(api_key) {
	document.querySelector("#save-button p").innerText = 'Saving';
	document.querySelector('#proxy-host-input').classList.remove('error-input');
	document.querySelector('#proxy-username-input').classList.remove('error-input');
	document.querySelector('#proxy-password-input').classList.remove('error-input');
	document.querySelector('#cv-input').classList.remove('error-input');
	document.querySelector("#flaresolverr-input").classList.remove('error-input');
	document.querySelector("#db-backup-folder-input").classList.remove("error-input");
	document.querySelector("#webhook-urls-input").classList.remove("error-input");

	let proxyIgnoredAddresses = document.querySelector('#proxy-ignored-addresses-input').value.split(',');
	if (proxyIgnoredAddresses[0] === '') {
		proxyIgnoredAddresses = []
	}
	const data = {
		'host': document.querySelector('#bind-address-input').value,
		'port': parseInt(document.querySelector('#port-input').value),
		'url_base': document.querySelector('#url-base-input').value,
		'auth_username': '',
		'auth_password': '',
		'proxy_type': document.querySelector('#proxy-type-input').value || null,
		'proxy_host': document.querySelector('#proxy-host-input').value,
		'proxy_port': parseInt(document.querySelector('#proxy-port-input').value),
		'proxy_username': document.querySelector('#proxy-username-input').value,
		'proxy_password': document.querySelector('#proxy-password-input').value,
		'proxy_ignored_addresses': proxyIgnoredAddresses,
		'comicvine_api_key': document.querySelector('#cv-input').value,
		'flaresolverr_base_url': document.querySelector('#flaresolverr-input').value,
		'log_level': parseInt(document.querySelector('#log-level-input').value),
		'db_backup_folder': document.querySelector("#db-backup-folder-input").value,
		'db_backup_amount': parseInt(document.querySelector("#db-backup-count-input").value),
		'webhook_urls': getWebhookUrls(),
		'webhook_secret': document.querySelector('#webhook-secret-input').value
	};
	
	const auth_toggle = document.querySelector('#auth-toggle');
	if (auth_toggle.value === 'username-password')
		data.auth_username = document.querySelector('#username-input').value;

	if (auth_toggle.value === 'username-password' || auth_toggle.value === 'password')
		data.auth_password = document.querySelector('#password-input').value;

	sendAPI('PUT', '/settings', api_key, {}, data)
	.then(response => response.json())
	.then(json => {
		document.querySelector("#save-button p").innerText = 'Saved';
	})
	.catch(async e => {
		document.querySelector("#save-button p").innerText = 'Failed';
		const json = await e.json();
		if (
			json.error === "InvalidKeyValue"
			&& json.result.key === "comicvine_api_key"
		)
			document.querySelector('#cv-input').classList.add('error-input');

		else if (
			json.error === "InvalidKeyValue"
			&& json.result.key === "proxy_host"
		)
			document.querySelector('#proxy-host-input').classList.add('error-input');

		else if (
			json.error === "InvalidKeyValue"
			&& json.result.key === "proxy_username"
		) {
			document.querySelector('#proxy-username-input').classList.add('error-input');
			document.querySelector('#proxy-password-input').classList.add('error-input');
		}

		else if (
			json.error === "InvalidKeyValue"
			&& json.result.key === "flaresolverr_base_url"
		)
			document.querySelector("#flaresolverr-input").classList.add('error-input');

		else if (
			json.error === "InvalidKeyValue"
			&& json.result.key === "webhook_urls"
		)
			document.querySelector("#webhook-urls-input").classList.add('error-input');

		else if (
			json.error === "FolderNotFound"
		)
			document.querySelector("#db-backup-folder-input").classList.add("error-input");

		else
			console.log(json.error);
	});
};

function getWebhookUrls() {
	return document.querySelector('#webhook-urls-input').value
		.split('\n')
		.map(u => u.trim())
		.filter(u => u);
};

function setWebhookStatus(text, state=null) {
	const status = document.querySelector('#webhook-status');
	status.innerText = text;
	status.classList.remove('success', 'error');
	if (state)
		status.classList.add(state);
};

function testWebhooks(api_key) {
	const urls = getWebhookUrls();
	if (!urls.length) {
		setWebhookStatus('Enter a URL first', 'error');
		return;
	};

	setWebhookStatus('Testing...');
	sendAPI('POST', '/webhooks/test', api_key, {}, {
		urls: urls,
		secret: document.querySelector('#webhook-secret-input').value
	})
	.then(response => response.json())
	.then(json => {
		const failed = json.result.filter(r => !r.success);
		if (!failed.length)
			setWebhookStatus('All URLs received the test event', 'success');
		else
			setWebhookStatus(
				'Failed: ' + failed.map(r => `${r.url} (${r.error})`).join(', '),
				'error'
			);
	})
	.catch(e => setWebhookStatus('Invalid URL, they have to start with http:// or https://', 'error'));
};

function generateApiKey(api_key) {
	sendAPI('POST', '/settings/api_key', api_key)
	.then(response => response.json())
	.then(json => {
		setLocalStorage({'api_key': json.result.api_key});
		document.querySelector('#api-input').innerText = json.result.api_key;
	});
};

// code run on load

usingApiKey()
.then(api_key => {
	fillSettings(api_key);
	document.querySelector('#save-button').onclick = e => saveSettings(api_key);
	document.querySelector('#generate-api').onclick = e => generateApiKey(api_key);
	document.querySelector('#webhook-test-button').onclick = e => testWebhooks(api_key);
	document.querySelector('#download-logs-button').href =
		`${url_base}/api/system/logs?api_key=${api_key}`;
});

document.querySelector('#theme-input').onchange = e => {
	const value = document.querySelector('#theme-input').value;
	setLocalStorage({'theme': value});
	setupTheme();
};
