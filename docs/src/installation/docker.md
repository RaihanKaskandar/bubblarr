# Docker

## Requirements

- [Docker](https://docs.docker.com/get-docker/) with Docker Compose (included in Docker Desktop).
- [Git](https://git-scm.com/), to download the source code.

## Install

Bubblarr isn't published as a ready-made image, so Docker builds it from the source code. The repository contains two Compose files:

| File | What it runs |
| ---- | ------------ |
| `docker-compose.torrents.yml` | Bubblarr, Prowlarr and qBittorrent, ready to work together. Optionally Jackett and FlareSolverr. Recommended. |
| `docker-compose.yml` | Only Bubblarr. Use this if you already run Prowlarr/Jackett and a torrent client. |

### Bubblarr with Prowlarr and qBittorrent

```bash
git clone <url of your Bubblarr repository> bubblarr
cd bubblarr
docker compose -f docker-compose.torrents.yml up -d --build
```

| App | URL |
| --- | --- |
| Bubblarr | http://localhost:5656 |
| Prowlarr | http://localhost:9696 |
| qBittorrent | http://localhost:8080 (user `admin`, a temporary password is shown in `docker logs qbittorrent`) |

All data is stored in the `data` folder next to the Compose file:

| Folder | Contents |
| ------ | -------- |
| `data/bubblarr` | The database of Bubblarr |
| `data/downloads` | The download folder, shared between Bubblarr and qBittorrent |
| `data/comics` | The comic library. Add `/comics` as root folder in Bubblarr. |
| `data/prowlarr`, `data/qbittorrent` | The config of Prowlarr and qBittorrent |

To use an existing comic library, change `./data/comics` in the Compose file to the folder of your library.

The download folder is mounted at the same path (`/app/temp_downloads`) in Bubblarr and qBittorrent, so no remote mapping is needed.

Jackett and FlareSolverr (needed for some sites behind Cloudflare) can be started too:

```bash
docker compose -f docker-compose.torrents.yml --profile jackett --profile flaresolverr up -d --build
```

When apps refer to each other, use the name of the container as the host (e.g. `http://qbittorrent:8080` and `http://prowlarr:9696`), not `localhost`.

### Only Bubblarr

Edit the paths in `docker-compose.yml` (`/path/to/...`) and run:

```bash
docker compose up -d --build
```

If your torrent client sees the download folder under a different path than Bubblarr, add a remote mapping in Settings → Download Clients.

## Run as a non-root user

The containers run with the user and group IDs set by `PUID` and `PGID`. In `docker-compose.torrents.yml` these default to 1000, which is the first user on most Linux systems. Override them with environment variables, e.g. `PUID=1001 PGID=1001 docker compose -f docker-compose.torrents.yml up -d`.

## Update

Pull the newest source code and rebuild:

```bash
git pull
docker compose -f docker-compose.torrents.yml up -d --build
```

The database is migrated automatically on start. A database of Kapowarr (`Kapowarr.db`) in the database folder is renamed to `Bubblarr.db` and used, so you can switch from Kapowarr by pointing Bubblarr to the database folder of Kapowarr.
