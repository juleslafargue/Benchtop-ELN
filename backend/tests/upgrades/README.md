# Data from every released version, for the upgrade test

Each `vX.Y.Z.zip` is a backup made by that version's own code (`python -m app.backup create`), of a demo lab made by
that version's own code (`app.demo_lab`; `app.seed` before 0.9.3), plus the extras listed in `fixtures.json`.
`tests/test_v24_upgrades.py` restores each one with today's code, upgrades it, and checks that nothing was lost or
changed.

- The attached files are kept once, in `files/` (most are the same from one version to the next), not in each zip.
  Backups made before 0.15 held their files inside the zip: `fixtures.json` lists them under `files_in_zip`, and
  the test puts them back before restoring.
- Made by `tests/make_upgrade_fixture.py`. When releasing a version, run it from `backend/` to add that version.
- Not in the release zip (`deploy/release.py` leaves this folder out): it's only for the tests.
- Don't edit these files by hand: the test checks every file's SHA-256 and each zip's database checksum.
