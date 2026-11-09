<p align="center">
    <img src="./frontend/static/img/favicon.svg" alt="Bubblarr" style="margin: 20px 0; width: 12rem;">
</p>

# Bubblarr

Bubblarr is a software to build and manage a comic book library, fitting in the *arr suite of software. It works like [Sonarr](https://sonarr.tv) and [Radarr](https://radarr.video), but for comics: it searches your torrent indexers and downloads with qBittorrent or Transmission.

Bubblarr is a fork of [Kapowarr](https://github.com/Casvt/Kapowarr) by Casvt, which searches [GetComics](https://getcomics.org) for downloads. Bubblarr keeps GetComics and adds torrent indexers next to it.

> [!WARNING]
> **The Bubblarr additions are vibe coded.**
> I built them for myself, for my own comic library, and they were written with an AI assistant rather than
> carefully coded by hand. The rest is Kapowarr's own work. I decided to put it online in case it is useful to
> someone else too.
>
> That means: no promise it won't break or that it will stay secure. Feel free to use it, but do so at your own risk.
> Back up your data, and expect rough edges. Issues and pull requests are welcome, but I can't promise a fast reply.

## Built on Kapowarr ❤️

Almost all of Bubblarr is [Kapowarr](https://github.com/Casvt/Kapowarr), built by Casvt and its contributors. Bubblarr
only adds a few things on top. If you find it useful, consider giving Kapowarr a ⭐ or
[supporting Casvt on Ko-fi](https://ko-fi.com/casvt). Please report problems with Bubblarr-specific features (like
torrent indexers) here, not to the Kapowarr project.

## Features

- Search torrent indexers the same way as Sonarr and Radarr
- Indexers sync automatically from Prowlarr, just like they do for Sonarr and Radarr (add Bubblarr as a "Mylar" app)
- Indexer priorities and minimum seeders, and results from all indexers ranked together
- Download torrents with qBittorrent or Transmission, or direct downloads via GetComics (Pixeldrain, Mega, MediaFire and more)
- Run a "Search Monitored" to download whole volumes with one click, or use "Manual Search" to decide yourself
- RSS sync picks up new releases on all indexers
- Import your existing library
- Upload comics from your device: Bubblarr recognises the series and issue from the metadata in the file or its filename, and puts it in the right folder under the right name
- Downloaded files are moved and renamed in the format you want, and archives can be extracted and converted
- A release calendar with upcoming issues, plus your pull list from League of Comic Geeks
- League of Comic Geeks sync: downloaded issues are marked as collected in your account
- Webhooks: get a signed POST when a volume is added or a download is imported
- A request API for apps like Seerr, used by the companion app Bubbleseerr
- A log viewer in the settings, with passwords and API keys hidden

## Running it

The quickest way is Docker. This builds Bubblarr from this repository and runs it together with Prowlarr and qBittorrent:

```bash
docker compose -f docker-compose.torrents.yml up -d --build
```

Then open Bubblarr on http://localhost:5656, Prowlarr on http://localhost:9696 and qBittorrent on http://localhost:8080. See the [installation documentation](docs/src/installation/installation.md) for the setup steps and other ways to install, and the [indexer documentation](docs/src/settings/indexers.md) for setting up Jackett and Prowlarr.

## Author

The Bubblarr additions were created by **Raihan Kaskandar** ([@RaihanKaskandar](https://github.com/RaihanKaskandar)),
who engineered the requirements, designed the architecture and the user interface, and directed the AI. The code for
them was written by Claude, an AI coding assistant.

## Credits and license

Bubblarr is based on [Kapowarr](https://github.com/Casvt/Kapowarr), made by Casvt and its contributors. Thank you for
building it and sharing it. Like Kapowarr, Bubblarr is licensed under the [GPL-3.0 license](LICENSE).
