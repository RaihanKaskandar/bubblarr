# Manual Install

## Requirements

- [Python](https://www.python.org/downloads/) 3.8 or newer, with pip.
- [Git](https://git-scm.com/), to download the source code.
- A torrent client (qBittorrent or Transmission) and Prowlarr or Jackett, if you want to use torrent indexers.

## Install

```bash
git clone <url of your Bubblarr repository> bubblarr
cd bubblarr
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python3 Bubblarr.py
```

On Windows, use `.venv\Scripts\pip` and `.venv\Scripts\python` instead.

Bubblarr is now available at http://localhost:5656. The database is stored in the `db` folder.

## Options

| Option | Description |
| ------ | ----------- |
| `-d`, `--DatabaseFolder` | The folder in which the database is stored |
| `-t`, `--TempDownloadFolder` | The download folder |
| `-l`, `--LogFolder` | The folder in which the logs are stored |
| `-f`, `--LogFile` | The filename of the log file |
| `-o`, `--Host` | The host to bind to |
| `-p`, `--Port` | The port to listen on |
| `-u`, `--UrlBase` | The URL base, e.g. `/bubblarr` |

Run `python3 Bubblarr.py --help` for all options.

## Run on startup

### Linux (systemd)

Create `/etc/systemd/system/bubblarr.service` (change the paths and user):

```ini
[Unit]
Description=Bubblarr
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/opt/bubblarr
ExecStart=/opt/bubblarr/.venv/bin/python3 /opt/bubblarr/Bubblarr.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then enable it with `sudo systemctl enable --now bubblarr`.

## Update

Stop Bubblarr, then:

```bash
git pull
.venv/bin/pip install -r requirements.txt
```

And start it again. The database is migrated automatically on start.
