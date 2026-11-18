# Project connections: the master keyring, rotation and loss

Issue #764 (WG-16), [ADR 0030](../adr/0030-project-connections-and-secrets.md). A project's stored secrets
are AES-256-GCM ciphertexts in `sbam.project_connections`. The keys that open them are one **keyring** that
never touches the database or git.

## The keyring

One line, comma separated: `key_id:base64(32 bytes)`, and the key that encrypts is marked with a leading `*`.

    *k2:<base64>,k1:<base64>

- Deployed it is the Docker secret file `sbam_secrets_keys` (`${SBAM_SECRETS_DIR}/sbam_secrets_keys`, mode 0400),
  exposed to the API as `SECRETS_KEYS_FILE`. `scripts/check-secrets.sh` checks it like every other secret.
- Make a key with `openssl rand -base64 32`. A key id is 1 to 32 letters, digits, `_` or `-`.
- The API refuses to start (outside development) if the keyring is missing, has no or several `*`, repeats an
  id, has a key that is not 32 bytes, or is the development placeholder.

## Rotate (planned)

1. Add the new key to the file and move the `*` to it: `*k2:...,k1:...`. Keep the old key.
2. Restart the API. Old rows still decrypt (each row names its `key_id`); new values use `k2`.
3. Dry run, then the re-encrypt, inside the API image (it reads the same settings and `DATABASE_URL`):

        python -m sbam_api.connections.rekey --dry-run
        python -m sbam_api.connections.rekey [--tenant UUID] [--batch 100]

   It prints `moved=N failed=N remaining=N`. Exit 0: clean. Exit 1: a row could not be decrypted (its key is
   gone) and is named on stderr as `name (key id)`. Exit 2: no usable keyring. Running it again moves nothing.
   Every moved row is audited as `CONNECTION_REKEYED` with both key ids; a moved value keeps its test result.
4. When `remaining=0`, remove the old key from the file and restart. Take a backup first if unsure.

## A key is compromised

Do the rotation above at once (step 1 with a new key), then ask every FDE to enter their values again if the
database or a backup may also have leaked: a ciphertext is only as safe as the key that opens it.

## A key is lost

The values sealed under it cannot be recovered, by design. `rekey` reports them by name and key id and leaves
them alone. Clear them (or let the FDEs overwrite them); every required one blocks Commit until entered again.

## Never

Do not put the keyring in `compose.yaml`, an image, a ticket or a log. The audit rows, the logs and the API
answers hold names, key ids and statuses, never a value (and never a masked tail in audit).
