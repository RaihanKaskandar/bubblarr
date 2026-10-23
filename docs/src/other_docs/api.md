---
hide:
  - navigation
---

# API documentation

All endpoints are under `/api` (after the URL base, if one is set) and return JSON in the form `{"error": null, "result": ...}`. On an error, `error` holds the name of the error and the HTTP status code is 4xx/5xx.

## Authentication

Every endpoint needs the API key, which can be found in Settings → General. Give it in the `X-Api-Key` header (preferred, like Sonarr and Radarr), or as the `api_key` URL parameter.

```bash
curl -H "X-Api-Key: <api key>" http://bubblarr:5656/api/volumes
```

## Request apps

These endpoints are meant for apps that let people request comics, the way Seerr does for Sonarr and Radarr. Such an app searches ComicVine through Bubblarr, shows which volumes and issues are already available, sends requests and gets notified through [webhooks](#webhooks) when they're downloaded.

Call these from the server of your app, not from the browser, so that the API key stays secret.

### Statuses

The status of a volume:

| Status | Meaning |
| ------ | ------- |
| `not_added` | The volume is not in the library. |
| `available` | All issues that are wanted and released are downloaded. |
| `partially_available` | Some issues are downloaded, but wanted issues are still missing. |
| `downloading` | One or more issues are being downloaded. |
| `wanted` | Wanted issues are missing, and none are downloaded yet. |
| `upcoming` | The volume is monitored, but its wanted issues aren't released yet. |
| `unmonitored` | Nothing is downloaded and the volume is not monitored. |

The status of an issue: `downloaded`, `downloading`, `missing` (released and wanted, but not downloaded), `unmonitored` (released, not wanted) or `upcoming` (not released yet, or no date known).

A library status object looks like this:

```json
{
    "volume_id": 3,
    "comicvine_id": 160294,
    "title": "Absolute Batman",
    "year": 2024,
    "volume_number": 1,
    "publisher": "DC Comics",
    "monitored": true,
    "monitor_new_issues": true,
    "status": "partially_available",
    "issues": {
        "total": 24, "downloaded": 18, "downloading": 1,
        "missing": 4, "upcoming": 1, "unmonitored": 0
    }
}
```

For volumes that are not in the library, it's `{"comicvine_id": 160294, "volume_id": null, "status": "not_added"}`.

### Search: `GET /api/lookup?query=<text>`

Search ComicVine for volumes. Every result is the ComicVine metadata (`comicvine_id`, `title`, `year`, `volume_number`, `publisher`, `issue_count`, `cover_link`, `description`, `site_url`, `aliases`, `translated`) plus a `library` key with the library status.

`cover_link` is a public ComicVine image URL. Covers of volumes in the library can also be fetched from `GET /api/volumes/<volume_id>/cover`.

### Volume details: `GET /api/lookup/<comicvine_id>`

The ComicVine metadata of one volume, including all its issues (`issues`), with the library status in `library`. Every issue also has a `library` key: `{"issue_id": 12, "monitored": true, "status": "downloaded"}`, or `null` when the volume isn't in the library.

### New releases: `GET /api/lookup/releases?start=YYYY-MM-DD&end=YYYY-MM-DD`

The comics with issues in stores in a period of up to 31 days, according to ComicVine, for a "New this week" list. Foreign editions are left out. Every volume has `comicvine_id`, `title`, `year`, `volume_number`, `publisher`, `cover_link`, `issue_count`, `issues` (the issues in the period: `comicvine_id`, `issue_number`, `date`) and `library` (the library status). The volumes with the most issues in the period come first. `limit` (100 to 3000, default 500) is the maximum amount of issues fetched from ComicVine; each 100 cost one ComicVine call. Results are cached for 6 hours to spare ComicVine's rate limit.

### Status of many volumes: `GET /api/lookup/status?comicvine_ids=1,2,3`

The library status of up to 500 volumes at once, keyed by ComicVine ID. Useful to refresh the status of a list of requests.

### Status of a library volume: `GET /api/volumes/<volume_id>/status`

The library status of a volume, with the extra key `issue_list` that has the status of every issue (`id`, `comicvine_id`, `issue_number`, `calculated_issue_number`, `title`, `date`, `monitored`, `status`).

### Request: `POST /api/request`

Request a volume or some of its issues. When the volume isn't in the library yet it's added, otherwise it's monitored again if needed. Then the requested issues are monitored and a search is started for the wanted issues that are missing.

```json
{
    "comicvine_id": 160294,
    "issues": "all",
    "root_folder_id": 1,
    "monitor_new_issues": true,
    "search": true
}
```

| Key | Required | Description |
| --- | -------- | ----------- |
| `comicvine_id` | Yes | The ComicVine ID of the volume. |
| `issues` | No | Which issues to monitor: `"all"` (default), `"missing"` (those that aren't downloaded), `"future"` (only issues that are released from now on) or a list of ComicVine IDs of issues. Issues that are already monitored stay monitored. |
| `root_folder_id` | No | The root folder to add a new volume to. Defaults to the first root folder (see `GET /api/rootfolder`). |
| `monitor_new_issues` | No | Whether to monitor issues that are released later. Defaults to keeping the current value, or for a new volume, `true` unless specific issues are requested. |
| `search` | No | Whether to start a search. Defaults to `true`. |

Returns the library status, with the extra keys `added` (whether the volume was added) and `searching` (whether a search was started). The status code is 201 when the volume was added and 200 otherwise.

Errors: `InvalidKeyValue` (400) for invalid values or issue IDs that aren't part of the volume, `RootFolderNotFound` (404) when there is no root folder, `VolumeNotMatched` (400) when the ComicVine ID doesn't exist, and `MetadataSourceRateLimitReached` (509) when ComicVine's rate limit is reached. The lookup endpoints can also return that last error.

## Webhooks

Set the webhook URLs in Settings → General → Webhooks. Bubblarr then POSTs a JSON event to every URL when something happens. The `X-Bubblarr-Event` header has the name of the event.

| Event | When |
| ----- | ---- |
| `volume.added` | A volume was added to the library (by any means). |
| `download.imported` | Downloaded issues were added to the library. For torrents that seed, this is when the files are copied to the library. |
| `download.failed` | A download failed. |
| `test` | The Test button in the settings was pressed. |

Example of `download.imported`:

```json
{
    "event": "download.imported",
    "version": 1,
    "timestamp": 1792281211,
    "volume": { "...": "the library status of the volume, see above" },
    "issues": [
        {
            "id": 12,
            "comicvine_id": 1094051,
            "issue_number": "19",
            "title": "The Place Beyond the Dark",
            "downloaded": true
        }
    ],
    "download": {
        "title": "Absolute Batman #19 (2026)",
        "source": "GetComics",
        "size": 52428800
    }
}
```

`volume.added` only has the `volume` key, and `download.failed` has the same keys as `download.imported`.

Webhooks are sent once, without retrying, with a timeout of 10 seconds. To not miss changes when your app was down, also refresh the status regularly with `GET /api/lookup/status`.

### Signature

When a secret is set, every webhook has the header `X-Bubblarr-Signature: sha256=<signature>`, where the signature is the hex HMAC-SHA256 of the raw request body with the secret as key. Check it to make sure a webhook comes from Bubblarr:

```python
import hmac, hashlib

def is_from_bubblarr(body: bytes, header: str, secret: str) -> bool:
    expected = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```

## Logs: `GET /api/system/logs/entries`

The most recent log entries (up to 5000 are kept, including the end of the log file from before the last restart), oldest first. This is what the Logs page in the settings shows. API keys and passwords in the messages are replaced by `[REDACTED]`.

| Parameter | Description |
| --------- | ----------- |
| `after` | Only entries with a higher ID than this. Use the `last_id` of the previous response to get only new entries. |
| `level` | The minimum level: `debug` (default), `info`, `warning` or `error`. Debug entries only exist when the log level is set to Debug. |
| `search` | Only entries that contain this text (not case sensitive). |
| `limit` | The maximum amount of entries, 1 to 5000 (default 500). The newest ones are returned. |

```json
{
    "instance": "1792282727000",
    "last_id": 6,
    "entries": [
        {
            "id": 4,
            "time": "2026-10-18T02:18:48+02:00",
            "level": "INFO",
            "thread": "Thread-12",
            "source": "search_full.pyL500",
            "message": "Starting auto search for Nightwing (2016) (volume 7)"
        }
    ]
}
```

`instance` changes when Bubblarr restarts. The IDs then start over, so when it changes, request everything again (without `after`). The complete log file can be downloaded with `GET /api/system/logs`.
