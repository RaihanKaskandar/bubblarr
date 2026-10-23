# Installation and Updating

## Installation Method

Bubblarr can be run with Docker or directly on your computer. The recommended method is Docker. Choose an installation method:

<div class="button-container">
	<a href="../docker">Docker</a>
	<a href="../manual_install">Manual Install</a>
</div>

After installing Bubblarr, there is some configuration needed so that it can start working and act to your preferences. It's advised to visit the [Setup After Installation page](./setup_after_installation.md) after installation for more information.

Instructions on how to update an installation can be found on the pages of the respective installation method.

## Quick Instructions

Bubblarr isn't published as a ready-made Docker image, so the image is built from the source code. With Git and Docker installed:

```bash
git clone <url of your Bubblarr repository> bubblarr
cd bubblarr
docker compose -f docker-compose.torrents.yml up -d --build
```

This starts Bubblarr (http://localhost:5656), Prowlarr (http://localhost:9696) and qBittorrent (http://localhost:8080). All data is stored in the `data` folder in the repository. Add `/comics` as your root folder in Bubblarr. See the [Docker page](./docker.md) for more details.
