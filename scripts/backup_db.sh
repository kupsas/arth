#!/usr/bin/env bash
# =============================================================================
# backup_db.sh — Daily SQLite backup for Arth
#
# WHAT IT DOES
#   1. Creates a timestamped copy of arth.db in data/backups/
#   2. Prunes backups older than 30 days (keeps latest 30)
#   3. Logs every action to data/logs/backup.log
#
# WHY .backup INSTEAD OF cp
#   SQLite's ".backup" command is the safe way to copy a live database.
#   A plain `cp` can catch the file mid-write and produce a corrupt copy.
#   The `.backup` command uses SQLite's internal API — it's atomic and
#   works correctly even if the API server or scraper is writing at the
#   same time.
#
# USAGE
#   Manual one-shot run:
#     bash scripts/backup_db.sh
#
#   Automated daily via launchd (see com.arth.backup.plist):
#     cp scripts/com.arth.backup.plist ~/Library/LaunchAgents/
#     launchctl load ~/Library/LaunchAgents/com.arth.backup.plist
#
# UNINSTALL LAUNCHD JOB
#     launchctl unload ~/Library/LaunchAgents/com.arth.backup.plist
#     rm ~/Library/LaunchAgents/com.arth.backup.plist
# =============================================================================

set -euo pipefail

# ── Paths (all relative to repo root, resolved from script location) ──────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

DB_SOURCE="$REPO_ROOT/data/arth.db"
BACKUP_DIR="$REPO_ROOT/data/backups"
LOG_FILE="$REPO_ROOT/data/logs/backup.log"

# ── Timestamp for this run ─────────────────────────────────────────────────
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DEST="$BACKUP_DIR/arth_${TIMESTAMP}.db"

# ── Logging helper ────────────────────────────────────────────────────────────
log() {
    # Writes to both stdout (so launchd captures it) and the log file.
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE"
}

# ── Pre-flight checks ─────────────────────────────────────────────────────────
if [ ! -f "$DB_SOURCE" ]; then
    log "ERROR: Source database not found at $DB_SOURCE — aborting"
    exit 1
fi

if [ ! -d "$BACKUP_DIR" ]; then
    mkdir -p "$BACKUP_DIR"
    log "Created backup directory: $BACKUP_DIR"
fi

# ── Run the backup ────────────────────────────────────────────────────────────
log "Starting backup: $DB_SOURCE → $BACKUP_DEST"

# sqlite3's .backup command creates a consistent snapshot even under concurrent
# writes.  The "main" keyword refers to the main (only) database in arth.db.
sqlite3 "$DB_SOURCE" ".backup '$BACKUP_DEST'"

# Verify the backup is a valid SQLite file (quick integrity check).
if sqlite3 "$BACKUP_DEST" "PRAGMA integrity_check;" | grep -q "^ok$"; then
    BACKUP_SIZE="$(du -h "$BACKUP_DEST" | cut -f1)"
    log "Backup complete: $BACKUP_DEST ($BACKUP_SIZE)"
else
    log "ERROR: Integrity check failed on $BACKUP_DEST — removing corrupt backup"
    rm -f "$BACKUP_DEST"
    exit 1
fi

# ── Prune old backups ─────────────────────────────────────────────────────────
# Keep only the 30 most recent backups.  If you have fewer than 30, nothing
# gets deleted.  The list is sorted newest-first by filename (timestamps sort
# lexicographically), so `tail` gives us the oldest ones to remove.
KEEP=30
BACKUP_COUNT="$(find "$BACKUP_DIR" -name "arth_*.db" | wc -l | tr -d ' ')"

if [ "$BACKUP_COUNT" -gt "$KEEP" ]; then
    TO_DELETE=$(( BACKUP_COUNT - KEEP ))
    log "Pruning $TO_DELETE old backup(s) (keeping latest $KEEP)..."

    # Sort by name (= sort by timestamp), take the oldest ones, delete them.
    find "$BACKUP_DIR" -name "arth_*.db" | sort | head -n "$TO_DELETE" | while read -r old_file; do
        rm "$old_file"
        log "  Deleted: $(basename "$old_file")"
    done
fi

log "Done. Total backups retained: $(find "$BACKUP_DIR" -name "arth_*.db" | wc -l | tr -d ' ')"
