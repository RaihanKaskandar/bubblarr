## Fetching

### Date Type

The type of release date that should be fetched for issues. Either the cover date or in-store date. The default is the in-store date: the day the issue is actually released (usually a Wednesday). The cover date is the date printed on the cover, which is the first day of a month, typically two months after the issue is released. If an issue doesn't have a date of the chosen type, the other type is used.

The release date is used in the calendar, and for matching downloads and files to issues. When this setting is changed, the info of all volumes is updated to fetch the dates of the new type.
## League of Comic Geeks

Connect your [League of Comic Geeks](https://leagueofcomicgeeks.com) (LoCG) account by entering your username and password. Use the "Test" button to check that logging in works.

League of Comic Geeks doesn't have an official API, so Bubblarr uses their website the same way a browser does. This can stop working when they change their website.

### Mark Downloaded Issues as Collected

When an issue is downloaded, it's added to your collection on LoCG. Issues that are already in your library are added by the daily "League of Comic Geeks Sync" task, or right away with the "Sync collection now" button.

To do this, every volume is matched to a series on LoCG. The title has to match exactly, the publisher has to match and the start year can differ by at most one year. That way translated editions and spin-offs (e.g. "Absolute Batman Ashcan") aren't used. Issues are matched on their issue number. Volumes that can't be found are searched for again after a week.

### Show Pull List in Calendar

Issues on your LoCG pull list get a "Pull" marker in the calendar. Pulled issues of series that are not in your library are shown too (with a dashed border), and link to the issue on LoCG.
