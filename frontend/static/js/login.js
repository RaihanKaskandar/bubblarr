const LoginEls = {
	form: document.querySelector('#login-form'),
	usernameContainer: document.querySelector('#username-container'),
	username: document.querySelector('#username-input'),
	password: document.querySelector('#password-input'),
	button: document.querySelector('#login-button'),
	error: document.querySelector('#error-message')
};

function showError(text) {
	LoginEls.error.innerText = text;
	LoginEls.error.classList.remove('hidden');
};

function loadFields() {
	// The form is visible straight away, so that it also shows when this
	// request fails or is slow (e.g. on a fresh iOS home screen web app)
	fetch(`${url_base}/api/public`)
	.then(response => response.json())
	.then(json => {
		if (json.result.authentication_method == 1) {
			// Password only
			LoginEls.usernameContainer.remove();
			LoginEls.username = null;
			LoginEls.password.focus();
		} else
			LoginEls.username.focus();
	})
	.catch(e => console.log(e));
};

function login() {
	LoginEls.error.classList.add('hidden');
	LoginEls.username?.classList.remove('invalid');
	LoginEls.password.classList.remove('invalid');

	const data = {'password': LoginEls.password.value};
	if (LoginEls.username !== null)
		data.username = LoginEls.username.value.trim();

	LoginEls.button.disabled = true;
	LoginEls.button.innerText = 'Logging In...';

	fetch(`${url_base}/api/auth`, {
		'method': 'POST',
		'headers': {'Content-Type': 'application/json'},
		'body': JSON.stringify(data)
	})
	.then(response => {
		if (!response.ok) return Promise.reject(response.status);
		return response.json();
	})
	.then(json => registerLogin(json.result.api_key))
	.catch(e => {
		LoginEls.button.disabled = false;
		LoginEls.button.innerText = 'Log In';
		if (e === 401) {
			LoginEls.username?.classList.add('invalid');
			LoginEls.password.classList.add('invalid');
			showError(
				LoginEls.username !== null
					? 'Incorrect username or password'
					: 'Incorrect password'
			);
			LoginEls.password.select();
		} else {
			console.log(e);
			showError("Couldn't reach Bubblarr, try again");
		};
	});
};

function registerLogin(api_key) {
	const data = readStorage();
	data.api_key = api_key;
	data.last_login = Date.now() / 1000;
	writeStorage(data);
	redirect();
};

function redirect() {
	const parameters = new URLSearchParams(window.location.search);
	let redirect_value = parameters.get('redirect') || `${url_base}/`;
	// Only redirect within Bubblarr
	if (!redirect_value.startsWith('/') || redirect_value.startsWith('//'))
		redirect_value = `${url_base}/`;
	window.location.href = redirect_value;
};

// code run on load

const url_base = document.querySelector('#url_base').dataset.value;

if (readStorage().theme === 'dark')
	document.querySelector(':root').classList.add('dark-mode');

LoginEls.form.addEventListener('submit', e => {
	e.preventDefault();
	login();
});

usingApiKey(false)
.then(api_key => {
	if (api_key)
		redirect();
	else
		loadFields();
})
.catch(e => {
	console.log(e);
	loadFields();
});
