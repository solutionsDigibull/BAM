# tusd (E4.2, #61)

Resumable uploads for the FDE portal. tusd stores the bytes in `storage/uploads/<upload id>` and
calls two **file hooks** in [`hooks/`](hooks): `pre-create` (before the upload exists) and
`post-finish` (once it is complete). Each hook posts tusd's event to the API at
`/api/v1/internal/tusd/hooks` with the `X-Tusd-Hook-Secret` header (`TUSD_HOOK_SECRET`) and prints
the API's answer, which is tusd's hook response. Why file hooks: tusd cannot add a fixed header to
its HTTP hooks, so a small script carries the secret.

| Step | What happens |
|---|---|
| FDE asks `POST /api/v1/projects/{id}/uploads` | files are checked (type, size, 20-file limit); the answer has a 15-minute token |
| browser creates the upload at `TUSD_PORT` with metadata `token`, `filename`, `project_id` | `pre-create` checks token, project, size, count and type against the announced file; the upload id becomes the announced file's id |
| last byte arrives | `post-finish` stores the file through `StorageBackend`, registers the document version (`UPLOADED`) and starts `DocumentIngestion` |

tusd flags worth knowing: `-max-size=15728640` (a second 15 MB guard), `-disable-download` and
`-disable-termination` (nothing may read an uploaded file before the scan, and a client cannot
delete uploads), `-hooks-enabled-events=pre-create,post-finish`. `post-finish` retries for about a
minute, because tusd does not retry a file hook.

Try it by hand: ask for a token with a real FDE login, then create and patch an upload with any tus
client (Uppy, `tus-js-client`) against `http://localhost:1080/files/`, sending the token, file name
and project id as upload metadata. Tests: `server/tests/api/test_uploads.py` (the API with fakes) and
`server/tests/integration/test_uploads_stack.py` (this stack).

## Abandoned uploads (E4.12, #220)

An upload that never finishes leaves its staged file (`storage/uploads/<id>` and `<id>.info`) and a
reservation row. The daily `CleanupAbandonedUploads` workflow (Temporal Schedule at 03:00 UTC, an hour
after the purge, created by the ingest worker) removes them:

- a reservation still `RESERVED` or `CREATED` after its hold ended loses its staged files;
- such a row is deleted 7 days after its hold ended;
- a staged file that no reservation owns is only reported in the log and in the run's audit row;
- finished uploads, documents and versions are never touched.

The run writes one `UPLOADS_CLEANED` audit row with the counts. A run that could not remove a file
fails, so it shows up; the next run tries again. Tests: `server/tests/unit/test_upload_cleanup.py`,
`server/tests/temporal/test_cleanup_abandoned.py` and `server/tests/integration/test_cleanup_stack.py`.
