// localStorage can be empty (e.g. a web app that was just added to the
// home screen on iOS gets its own storage) or unavailable, so never assume
// that it's filled.
function readStorage() {
	try {
		const data = JSON.parse(localStorage.getItem('bubblarr'));
		if (data && typeof data === 'object')
			return data;
	} catch (e) {};
	return {'api_key': null, 'last_login': 0};
};

function writeStorage(data) {
	try {
		localStorage.setItem('bubblarr', JSON.stringify(data));
	} catch (e) {
		console.log(e);
	};
};

async function usingApiKey(redirect=true) {
	const key_data = readStorage();

	if (!key_data.api_key
	|| (key_data.last_login < (Date.now() / 1000 - 86400))) {

		return fetch(`${url_base}/api/auth`, {
			'method': 'POST',
			'headers': {'Content-Type': 'application/json'},
			'body': '{}'
		})
			.then(response => {
				if (!response.ok) return Promise.reject(response.status);
				return response.json();
			})
			.then(json => {
				key_data.api_key = json.result.api_key;
				key_data.last_login = Date.now() / 1000;
				writeStorage(key_data);
				return json.result.api_key;
			})
			.catch(e => {
				if (e === 401) {
					if (redirect) window.location.href = `${url_base}/login?redirect=${window.location.pathname}`;
					else return null;
				} else {
					console.log(e);
					return null;
				};
			})

	} else {
		return key_data.api_key;
	};
};
