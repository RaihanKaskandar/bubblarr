## Built-in Clients

A list of the download clients Bubblarr has built-in. It uses these to download from multiple download services used by GetComics. Clicking on one of them shows a window with more information and, if the client has support for it, an option to enter credentials (see below).

### Credentials

If you have an account with Mega or Pixeldrain, Bubblarr has the ability to use this account. If you provide your login credentials for the service, Bubblarr will then take advantage of the extra features that your account has access to (higher speeds and limits, usually). You can enter the credentials by clicking on the client and filling in the form.

## Torrent Clients

By adding at least one torrent client, Bubblarr is able to download torrents.

!!! warning "Using localhost in combination with a Docker container"
    If the torrent client is hosted on the host OS, and Bubblarr is running inside a Docker container, then it is not possible to use `localhost` in the base URL of the torrent client. Instead, the IP address used by the host OS must be used.

## Remote Path Mappings

When a remote download client and Bubblarr do not run on the same machine (because at least one of them is on another machine or in a Docker container), then filepaths could not match up. For example, a torrent client might be running in a Docker container where the download folder is at `/downloads` which maps to `/home/media/downloads` on the host while Bubblarr is running on the host. Then when Bubblarr reaches out to the torrent client, it'll report that the file is located at `/downloads/file.ext`. But Bubblarr can actually find it at `/home/media/downloads/file.ext`. 'Remote Path Mappings' allows you to add such a mapping so that Bubblarr can translate filepaths that are reported by the external download client to where it can actually find it.

When adding a remote mapping, choose the client it's for and then set the paths. The remote path is the path that the client reports (e.g. `/downloads`). The local path is the path that Bubblarr should actually look (e.g. `/home/media/downloads`).

!!! warning "Cross-OS remote mapping"
    Bubblarr currently does not support remote mappings between different operating systems. So for example, if a client is running on Windows and Bubblarr on Linux, then it currently doesn't support translating those. 
