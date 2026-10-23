This settings page allows you to manage your download indexers. Aside from the standard options, there are options that are specific to certain types of indexers. These are listed below.

## GetComics

### Avoid Large Downloads

GetComics offers downloads directly from their servers, but throttles the download speed significantly after downloading the first 400MB of a file. If the file is smaller than 400MB, then the download speed is never throttled. When this option is enabled, then if a file is larger than 400MB, try all external services (like Mega, MediaFire, etc.) first before resorting to downloading directly from the servers of GetComics.

### Service Preference

GetComics not only offers downloads directly from their servers, but also via services like Mega and MediaFire. When a download on GetComics is found and has multiple possible download services, this defines which service takes priority. If the first download fails, Bubblarr will try the next service in order.

If you have an account for one of these services (see [Credentials](./downloadclients.md#credentials) setting), you might want to put that one at the top, to make Bubblarr take advantage of the extra features that the account offers (extra bandwidth, higher rate limit, etc.).  

### Indexer Priority

When two releases are equally good, the one from the indexer with the highest priority (lowest number, 1-50) is preferred. This applies to all indexers, including GetComics. For torrents with the same priority, the release with the most seeders is preferred.

## Torrent (Torznab)

Torrent indexers are added via [Jackett](https://github.com/Jackett/Jackett) or [Prowlarr](https://prowlarr.com), the same way as in Sonarr and Radarr. Found torrents are sent to the torrent client set under [Download Clients](./downloadclients.md) (qBittorrent or Transmission).

| Setting | Description |
| ------- | ----------- |
| URL | The Torznab feed URL. Both the feed URL (`.../torznab/`) and the API URL (`.../torznab/api`) work. |
| API Key | The API key of Jackett or Prowlarr. |
| Categories | Comma separated list of the Newznab categories to search in. Default is `7030` (Books/Comics). |
| Minimum Seeders | Releases with less seeders are ignored. Default is 1. |
| Indexer Priority | See above. |

Torrent indexers are also used for the RSS sync (download discovery): new releases in the feed are matched against your monitored volumes.

### Jackett

Add an indexer in Jackett, click "Copy Torznab Feed" and use that as the URL. Use the API key shown at the top of the Jackett dashboard. To search all indexers in Jackett at once, use the "all" feed: `http://jackett:9117/api/v2.0/indexers/all/results/torznab/`.

### Prowlarr (manual)

Add a Torznab indexer with the URL `http://prowlarr:9696/{id}/`, where `{id}` is the ID of the indexer in Prowlarr, and the Prowlarr API key.

### Prowlarr (automatic sync)

Bubblarr implements the part of the Mylar API that Prowlarr uses to sync indexers. This makes Prowlarr manage the indexers in Bubblarr, like it does for Sonarr and Radarr:

1. In Prowlarr, go to Settings → Apps and add a **Mylar** application.
2. Set "Prowlarr Server" to the URL on which Bubblarr can reach Prowlarr.
3. Set "Mylar Server" to the URL of Bubblarr (including the URL base, if you set one).
4. Set "API Key" to the API key of Bubblarr (Settings → General).
5. Keep the sync categories on `7030` (Books/Comics).

Prowlarr will then add, update and remove its indexers in Bubblarr. These indexers are marked with a "Prowlarr" badge. The title, URL, API key, categories and enabled state are managed by Prowlarr; the minimum seeders and priority can be changed in Bubblarr. Only torrent indexers are supported, Usenet indexers are skipped.
