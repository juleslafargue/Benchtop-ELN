#!/usr/bin/env bash
# Update a Benchtop lab server (the Docker setup in docs/SERVER.md) to the newest release, in one go:
# show what's new, back up the data with the running version, move to the new version, rebuild, restart,
# and check it answers. Run it on the server, from anywhere:
#
#   deploy/update-server.sh              update (asks first; --yes doesn't)
#   deploy/update-server.sh --check      only show what's new
#   deploy/update-server.sh --rollback   go back to the version from before the last update
#
# The Postgres setup: BENCHTOP_COMPOSE_FILE=deploy/docker-compose.server-postgres.yml deploy/update-server.sh --no-backup
# (after taking your own pg_dump, since Benchtop can't back up Postgres itself).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
FILE="${BENCHTOP_COMPOSE_FILE:-deploy/docker-compose.server.yml}"
DC=(docker compose -f "$FILE" --env-file deploy/.env)
STATE="deploy/.previous-version"

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

MODE=update; YES=""; NO_BACKUP=""
for a in "$@"; do
  case "$a" in
    --check) MODE=check ;; --rollback) MODE=rollback ;; -y|--yes) YES=1 ;; --no-backup) NO_BACKUP=1 ;;
    *) fail "Usage: deploy/update-server.sh [--check | --rollback] [--yes] [--no-backup]" ;;
  esac
done

[ -f deploy/.env ] || fail "deploy/.env is missing. This updates a server set up as docs/SERVER.md describes."
command -v docker >/dev/null 2>&1 || fail "Docker isn't installed here."
[ -d .git ] && command -v git >/dev/null 2>&1 ||
  fail "This folder isn't a Git clone, so it can't fetch new versions. Follow 'Updating' in docs/SERVER.md instead."

confirm() {
  [ -n "$YES" ] && return 0
  [ -t 0 ] || fail "Add --yes to go ahead without being asked. Nothing was changed."
  printf '%s [y/N] ' "$1"; local ans; read -r ans || ans=""
  case "$ans" in y|Y|yes|Yes|YES) ;; *) say "Nothing was changed."; exit 0 ;; esac
}

version_at() { git show "$1:backend/app/__init__.py" | sed -nE 's/^__version__ = "([^"]+)".*/\1/p'; }

running_version() {
  "${DC[@]}" exec -T app python -c \
    "import json, urllib.request; print(json.load(urllib.request.urlopen('http://localhost:8000/api/health', timeout=5))['version'])" 2>/dev/null
}

wait_for() {  # until the app answers with version $1 (about five minutes at most)
  local v
  for _ in $(seq 1 100); do
    v="$(running_version || true)"
    [ "$v" = "$1" ] && return 0
    sleep 3
  done
  return 1
}

why_not_up() {
  local log; log="$("${DC[@]}" logs --tail 40 app 2>&1 || true)"
  printf '%s\n' "$log" | tail -15
  printf '%s\n' "$log" | grep -q "can't open this data" && return 0 || return 1
}

backup_now() {
  BACKUP=""
  if [ -n "$NO_BACKUP" ]; then say "Skipping the backup (--no-backup)."; return 0; fi
  say "Backing up the data first, with the version that's running now…"
  local out
  if [ -n "$("${DC[@]}" ps -q --status running app 2>/dev/null)" ]; then
    out="$("${DC[@]}" exec -T app python -m app.backup create 2>&1)" || {
      printf '%s\n' "$out"
      printf '%s\n' "$out" | grep -q "SQLite" &&
        fail "This server keeps its records in Postgres, which Benchtop can't back up itself. Take a pg_dump (docs/SERVER.md), then run this again with --no-backup. Nothing was changed."
      fail "The backup didn't work, so nothing was changed."; }
  else
    out="$("${DC[@]}" run --rm --no-deps app python -m app.backup create 2>&1)" || {
      printf '%s\n' "$out"; fail "The backup didn't work (Benchtop isn't running, so it was tried in a fresh container). Nothing was changed."; }
  fi
  printf '%s\n' "$out" | grep -E "^(Backup written|Warning)" || printf '%s\n' "$out" | tail -3
  BACKUP="$(printf '%s\n' "$out" | sed -nE 's/^Backup written: (.*) \([0-9.]+ MB.*/\1/p')"
}

branch="$(git symbolic-ref --short -q HEAD || true)"
[ "$branch" = main ] || fail "This copy is on '${branch:-an older commit}', not main. Run: git switch main, then this again. Nothing was changed."
changed="$(git status --porcelain --untracked-files=no)"
if [ -n "$changed" ]; then
  printf '%s\n' "$changed" | sed 's/^/   /'
  fail "Benchtop's own files (above) were changed on this server. Commit or undo those changes (git stash), then run this again. Nothing was changed."
fi

if [ "$MODE" = rollback ]; then
  [ -f "$STATE" ] || fail "There's no earlier version to go back to (this keeps the one from before the latest update)."
  . "$STATE"
  say "Going back from Benchtop $NEW_VERSION to $OLD_VERSION (from before the update on $WHEN). The data stays as it is."
  confirm "Go back to $OLD_VERSION?"
  git reset --quiet --keep "$OLD_REV" || fail "Git couldn't go back (see above). Nothing was changed."
  say "Building and restarting $OLD_VERSION (a few minutes)…"
  "${DC[@]}" up -d --build
  if ! wait_for "$OLD_VERSION"; then
    if why_not_up; then
      say "Benchtop $OLD_VERSION can't open the data as $NEW_VERSION left it."
      echo "To use $OLD_VERSION, restore the backup made just before the update (anything recorded since then is lost):"
      echo "  ${DC[*]} stop app"
      echo "  ${DC[*]} run --rm app python -m app.backup restore ${BACKUP:-/backups/<the backup from $WHEN>}"
      echo "  ${DC[*]} up -d"
      echo "Or go forward again: deploy/update-server.sh"
      exit 1
    fi
    fail "Benchtop $OLD_VERSION didn't come up (its log is above)."
  fi
  rm -f "$STATE"
  say "Back on Benchtop $OLD_VERSION, running. deploy/update-server.sh brings the newer version back when you're ready."
  exit 0
fi

say "Looking for a newer version on GitHub…"
git fetch --quiet --tags origin main || fail "Couldn't reach GitHub. Nothing was changed."
# releases are tagged vX.Y.Z once GitHub's tests have passed; take the newest (main, until the first release)
latest="$(git tag -l 'v[0-9]*' --sort=-v:refname | head -1)"
target="${latest:-origin/main}"
OLDV="$(version_at HEAD)"
if git merge-base --is-ancestor "$target" HEAD; then say "This server has the newest version ($OLDV)."; exit 0; fi
git merge-base --is-ancestor HEAD "$target" ||
  fail "This copy has commits that aren't on GitHub, so it can't simply move forward to $target. Nothing was changed."
NEWV="$(version_at "$target")"
say "Benchtop $NEWV is available (this server has $OLDV). What's new:"
git log --no-merges --format='  • %s' "HEAD..$target" | head -40
[ "$MODE" = check ] && exit 0
confirm "Update to $NEWV now? The data is backed up first; people can't use Benchtop for the few minutes of the restart."

backup_now
printf 'OLD_VERSION=%q\nNEW_VERSION=%q\nOLD_REV=%q\nBACKUP=%q\nWHEN=%q\n' \
  "$OLDV" "$NEWV" "$(git rev-parse HEAD)" "$BACKUP" "$(date '+%Y-%m-%d %H:%M')" > "$STATE"
git merge --ff-only --quiet "$target" || fail "Git couldn't move to $NEWV. Nothing was changed."
say "Building and restarting (a few minutes)…"
"${DC[@]}" up -d --build
if ! wait_for "$NEWV"; then
  why_not_up || true
  fail "Benchtop $NEWV didn't come up (its log is above). deploy/update-server.sh --rollback goes back to $OLDV."
fi
say "Updated: Benchtop $OLDV -> $NEWV, running."
[ -n "$BACKUP" ] && echo "The data was backed up first: $BACKUP (in the backups folder on this server)."
echo "If something isn't right: deploy/update-server.sh --rollback"
