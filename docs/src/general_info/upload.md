The 'Upload' feature makes it possible to add a comic file from your own device to your library, for example from your phone or laptop. Bubblarr recognises which volume and issue the file is for, moves it to the right folder and gives it the same kind of name as the other files in your library. You can find the feature in the web-UI at Volumes -> Upload.

!!! info "Upload versus Library Import"
    [Library Import](./library_import.md) is for files that are already on the server, and it can add volumes that aren't in your library yet. Upload is for files that are on your device, and it only matches to volumes that are already in your library. If the volume isn't in your library yet, add it first.

## Uploading

Drop the files on the page, or click to choose them. CBZ, CBR, CB7, CBT, CBA, ZIP, RAR, 7Z, PDF, EPUB and MOBI files can be uploaded. Files are sent in small pieces, so reverse proxies that limit the size of a request (Cloudflare, for example) aren't a problem and short connection drops are recovered from automatically. Keep the page open while files are uploading.

A file waits in a sub-folder of the download folder until you import it. You can close the page and come back to it later: files that are uploaded but not imported yet are kept for 7 days. Uploads that were never finished are deleted after a day.

## How a file is recognised

Bubblarr tries these in order:

1. **The metadata inside the file.** Tools like ComicTagger and ComicRack can store a `ComicInfo.xml` file inside CBZ and CBR files. If it contains a ComicVine ID of an issue or volume in your library, then the match is certain. Otherwise, the series, issue number, volume and year in the metadata are used.
2. **The filename.** The filename is read the same way as for a download, see [Matching](./matching.md).

Every file gets one of these labels:

- **Recognised**: One volume in your library fits and the issue exists in it. The file is selected for import, unless the issue already has a file.
- **Check this**: Bubblarr has a best guess but isn't sure. For example, because two volumes have the same title and the file doesn't say which one it is for, or because the year doesn't fit. The file is not selected for import, but the reason is shown.
- **Not recognised**: Nothing in your library resembles the file. If you added the volume in the meantime, reload the page to try again.

## Importing

Use 'Change' to correct the volume or issue. A file can cover multiple issues in a row. If you change the volume, you can also apply it to the other files of the same series, which is useful when a batch of files wasn't recognised because the series has a different name in the filenames.

Clicking 'Import' moves the selected files into their volume folder. The destination is shown for each file. The name of the file is determined by the [naming settings](../settings/mediamanagement.md#file-naming), regardless of the 'Rename Downloaded Files' setting. After that, the file is handled like a download: it's converted if you've set that up, and it shows up in the history with 'Upload' as the source.

An existing file is never replaced. If an issue already has a file then this is shown, the upload isn't selected by default, and when you import it anyway the file is added next to the existing one. You can then delete the file you don't want from the page of the volume.
