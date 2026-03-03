#!/bin/bash
# =============================================================================
# Fix NFO mismatches across all affected series
# Fixes episode/season tags in NFO files to match filenames
# For completely broken NFOs: deletes them so Emby re-identifies from filename
# =============================================================================

set -uo pipefail
DRY_RUN=${DRY_RUN:-1}

run() {
    if [ "$DRY_RUN" = "1" ]; then
        echo "  [DRY] $*"
    else
        eval "$@" 2>/dev/null
        echo "  [OK] $*"
    fi
}

fix_nfo_ep_season() {
    # Fix <episode> and <season> tags in an NFO file to match filename
    local nfo="$1"
    local correct_season="$2"
    local correct_episode="$3"

    if [ ! -f "$nfo" ]; then
        echo "  [SKIP] NFO not found: $nfo"
        return
    fi

    run "sed -i 's|<episode>[0-9]*</episode>|<episode>${correct_episode}</episode>|' \"$nfo\""
    run "sed -i 's|<season>[0-9]*</season>|<season>${correct_season}</season>|' \"$nfo\""
}

delete_nfo() {
    local nfo="$1"
    if [ -f "$nfo" ]; then
        run "rm \"$nfo\""
    fi
}

delete_all_nfo_in_dir() {
    local dir="$1"
    if [ -d "$dir" ]; then
        local count=$(find "$dir" -name "*.nfo" -not -name "tvshow.nfo" -not -name "season.nfo" | wc -l)
        echo "  Deleting $count episode NFO files in $dir"
        run "find \"$dir\" -name '*.nfo' -not -name 'tvshow.nfo' -not -name 'season.nfo' -delete"
    fi
}

echo "========================================="
echo "  NFO Mismatch Fix Script"
echo "  DRY_RUN=$DRY_RUN"
echo "========================================="

BASE="/Movies/Serials"
DOCS="/Movies/Dokumenty"

# -------------------------------------------------------------------
# 1. Pat & Mat — 93 episodes. TVDB maps all to S01, files have S02-S08
#    Fix: delete all episode NFOs, let Emby use filenames
# -------------------------------------------------------------------
echo ""
echo "=== Pat & Mat (93 mismatches) — delete episode NFOs ==="
for d in "$BASE/--- PREBIEHAJUCE ---/Pat & Mat (1976)"/S*; do
    [ -d "$d" ] && delete_all_nfo_in_dir "$d"
done

# -------------------------------------------------------------------
# 2. MythBusters — 256 mismatches. TVDB uses year-based seasons (S2003)
#    Fix: delete all episode NFOs
# -------------------------------------------------------------------
echo ""
echo "=== MythBusters (256 mismatches) — delete episode NFOs ==="
for d in "$BASE/--- UKONCENE ---/Mythbusters"/S*; do
    [ -d "$d" ] && delete_all_nfo_in_dir "$d"
done
delete_all_nfo_in_dir "$BASE/--- UKONCENE ---/Mythbusters"

# -------------------------------------------------------------------
# 3. Generation Kill — 4 eps mapped to S20E08
#    Fix: delete broken NFOs
# -------------------------------------------------------------------
echo ""
echo "=== Generation Kill (4 mismatches) — delete broken NFOs ==="
D="$BASE/--- UKONCENE ---/Generation Kill"
[ -d "$D" ] && delete_all_nfo_in_dir "$D/S01" 2>/dev/null
[ -d "$D" ] && delete_all_nfo_in_dir "$D"

# -------------------------------------------------------------------
# 4. Hackerville — 2 eps mapped to S20E18
# -------------------------------------------------------------------
echo ""
echo "=== Hackerville (2 mismatches) — delete broken NFOs ==="
D="$BASE/--- UKONCENE ---/Hackerville"
[ -d "$D" ] && delete_all_nfo_in_dir "$D/S01" 2>/dev/null
[ -d "$D" ] && delete_all_nfo_in_dir "$D"

# -------------------------------------------------------------------
# 5. A Perfect Planet — 2 eps mapped to S20/S2021
# -------------------------------------------------------------------
echo ""
echo "=== A Perfect Planet (2 mismatches) — delete broken NFOs ==="
D="$DOCS/A Perfect Planet"
[ ! -d "$D" ] && D=$(find "$DOCS" -maxdepth 1 -name "A Perfect Planet*" -type d | head -1)
[ -d "$D" ] && delete_all_nfo_in_dir "$D"

# -------------------------------------------------------------------
# 6. Red Line / Rédl — S01 mapped to S02E64
# -------------------------------------------------------------------
echo ""
echo "=== Red Line (2 mismatches) — delete broken NFOs ==="
for D in "$BASE/--- UKONCENE ---/Red Line" "$BASE/--- UKONCENE ---/Redl" "$BASE/--- PREBIEHAJUCE ---/Redl"*; do
    [ -d "$D" ] && delete_all_nfo_in_dir "$D"
done

# -------------------------------------------------------------------
# 7. The Power — S01E01 mapped to S02E65
# -------------------------------------------------------------------
echo ""
echo "=== The Power (1 mismatch) — delete broken NFOs ==="
for D in "$BASE/--- UKONCENE ---/The Power" "$BASE/--- PREBIEHAJUCE ---/The Power"; do
    [ -d "$D" ] && delete_all_nfo_in_dir "$D"
done

# -------------------------------------------------------------------
# 8. Twisted Metal — S02E51 mapped to S02E51080 (broken)
# -------------------------------------------------------------------
echo ""
echo "=== Twisted Metal (1 mismatch) — delete broken NFOs ==="
for D in "$BASE"/*/Twisted\ Metal*; do
    [ -d "$D" ] && delete_all_nfo_in_dir "$D"
done

# -------------------------------------------------------------------
# 9. Chicago Party Aunt — all S02 mapped to S01E01
# -------------------------------------------------------------------
echo ""
echo "=== Chicago Party Aunt (7 mismatches) — delete broken NFOs ==="
for D in "$BASE"/*/Chicago\ Party\ Aunt*; do
    [ -d "$D" ] && delete_all_nfo_in_dir "$D"
done

# -------------------------------------------------------------------
# 10. Steven Universe — episode numbering shifted
# -------------------------------------------------------------------
echo ""
echo "=== Steven Universe (56 mismatches) — delete episode NFOs ==="
for D in "$BASE"/*/Steven\ Universe*; do
    [ -d "$D" ] && delete_all_nfo_in_dir "$D"
done

# -------------------------------------------------------------------
# 11. Labková patrola — some episode shifts
# -------------------------------------------------------------------
echo ""
echo "=== Labková patrola (5 mismatches) — fix specific NFOs ==="
D="$BASE/--- PREBIEHAJUCE ---/Tlapkova patrola - 1080p WEB-DL CZ"
[ -d "$D" ] && {
    fix_nfo_ep_season "$D/S03/Tlapkova patrola S03E49 - 1080p WEB-DL CZ.nfo" 3 49
    fix_nfo_ep_season "$D/S05/Tlapkova patrola S05E48 - 1080p WEB-DL CZ.nfo" 5 48
    fix_nfo_ep_season "$D/S05/Tlapkova patrola S05E49 - 1080p WEB-DL CZ.nfo" 5 49
    fix_nfo_ep_season "$D/S07/Tlapkova patrola S07E46 - 1080p WEB-DL CZ.nfo" 7 46
    fix_nfo_ep_season "$D/S08/Tlapkova patrola S08E46 - 1080p WEB-DL CZ.nfo" 8 46
}

# -------------------------------------------------------------------
# 12. All Creatures Great & Small — S02E06 mapped to S02E07
# -------------------------------------------------------------------
echo ""
echo "=== All Creatures Great & Small (1 mismatch) — fix NFO ==="
D="$BASE/--- PREBIEHAJUCE ---/All Creatures Great and Small"
[ -d "$D" ] && {
    NFO=$(find "$D" -name "*S02E06*nfo" 2>/dev/null | head -1)
    [ -n "$NFO" ] && fix_nfo_ep_season "$NFO" 2 6
}

# -------------------------------------------------------------------
# 13. Horná Dolná — mixed season mapping
# -------------------------------------------------------------------
echo ""
echo "=== Horná Dolná (6 mismatches) — delete affected NFOs ==="
D="$BASE/--- PREBIEHAJUCE ---/Horna dolna"
[ -d "$D" ] && delete_all_nfo_in_dir "$D"

# -------------------------------------------------------------------
# 14. My Happy Marriage — S01E13 mapped to S02E01
# -------------------------------------------------------------------
echo ""
echo "=== My Happy Marriage (1 mismatch) — fix NFO ==="
for D in "$BASE"/*/My\ Happy\ Marriage*; do
    [ -d "$D" ] && {
        NFO=$(find "$D" -name "*S01E13*nfo" 2>/dev/null | head -1)
        [ -n "$NFO" ] && fix_nfo_ep_season "$NFO" 1 13
    }
done

# -------------------------------------------------------------------
# 15. Nip/Tuck — S06 eps mapped to S07
# -------------------------------------------------------------------
echo ""
echo "=== Nip/Tuck (3 mismatches) — fix season in NFOs ==="
for D in "$BASE"/*/Nip*Tuck*; do
    [ -d "$D" ] && {
        for NFO in "$D"/S06/*.nfo; do
            [ -f "$NFO" ] && {
                # Check if NFO says season 7, fix to 6
                grep -q "<season>7</season>" "$NFO" 2>/dev/null && fix_nfo_ep_season "$NFO" 6 "$(grep -o '<episode>[0-9]*</episode>' "$NFO" | grep -o '[0-9]*')"
            }
        done
    }
done

# -------------------------------------------------------------------
# 16. On the Roam — off by one
# -------------------------------------------------------------------
echo ""
echo "=== On the Roam (1 mismatch) — fix NFO ==="
D="$DOCS/On the Roam (2024) - 1080p x264"
[ -d "$D" ] && {
    NFO=$(find "$D" -name "*S01E03*nfo" 2>/dev/null | head -1)
    [ -n "$NFO" ] && fix_nfo_ep_season "$NFO" 1 3
}

# -------------------------------------------------------------------
# 17. Pán času / Doctor Who — specials (S00) chaos, delete NFOs
# -------------------------------------------------------------------
echo ""
echo "=== Doctor Who / Pán času (11 mismatches) — delete episode NFOs ==="
for D in "$BASE"/*/Doctor\ Who* "$BASE"/*/"Pan Casu"* "$BASE"/*/"Pán času"* "$BASE"/*/"Pán Času"*; do
    [ -d "$D" ] && delete_all_nfo_in_dir "$D"
done

# -------------------------------------------------------------------
# 18. Pohlreichův souboj restaurací — S01E05 mapped to S01E04
# -------------------------------------------------------------------
echo ""
echo "=== Pohlreichův souboj (1 mismatch) — fix NFO ==="
for D in "$BASE"/*/Pohlreich* "$DOCS"/Pohlreich*; do
    [ -d "$D" ] && {
        NFO=$(find "$D" -name "*S01E05*nfo" 2>/dev/null | head -1)
        [ -n "$NFO" ] && fix_nfo_ep_season "$NFO" 1 5
    }
done

# -------------------------------------------------------------------
# 19. The Resident — S02 files mapped to S01
# -------------------------------------------------------------------
echo ""
echo "=== The Resident (3 mismatches) — fix season in NFOs ==="
for D in "$BASE"/*/The\ Resident*; do
    [ -d "$D/S02" ] && {
        for NFO in "$D"/S02/*.nfo; do
            [ -f "$NFO" ] && {
                grep -q "<season>1</season>" "$NFO" 2>/dev/null && {
                    EP=$(grep -o '<episode>[0-9]*</episode>' "$NFO" | grep -o '[0-9]*')
                    fix_nfo_ep_season "$NFO" 2 "$EP"
                }
            }
        done
    }
done

# -------------------------------------------------------------------
# 20. Star Trek: Voyager — S01E02 mapped to S01E01 (double ep)
# -------------------------------------------------------------------
echo ""
echo "=== Star Trek: Voyager (1 mismatch) — fix NFO ==="
for D in "$BASE"/*/Star\ Trek\ Voyager* "$BASE"/*/"Star Trek: Voyager"*; do
    [ -d "$D" ] && {
        NFO=$(find "$D" -name "*S01E02*nfo" 2>/dev/null | head -1)
        [ -n "$NFO" ] && fix_nfo_ep_season "$NFO" 1 2
    }
done

# -------------------------------------------------------------------
# 21. Timeless — S02E10 mapped to S02E11
# -------------------------------------------------------------------
echo ""
echo "=== Timeless (1 mismatch) — fix NFO ==="
for D in "$BASE"/*/"Cestovatelia v čase"* "$BASE"/*/Timeless*; do
    [ -d "$D" ] && {
        NFO=$(find "$D" -name "*S02E10*nfo" 2>/dev/null | head -1)
        [ -n "$NFO" ] && fix_nfo_ep_season "$NFO" 2 10
    }
done

# -------------------------------------------------------------------
# 22. Máme rádi Česko — S10E06 mapped to S11E06
# -------------------------------------------------------------------
echo ""
echo "=== Máme rádi Česko (1 mismatch) — fix NFO ==="
for D in "$BASE"/*/"Máme rádi"* "$BASE"/*/"Mame radi"*; do
    [ -d "$D" ] && {
        NFO=$(find "$D" -name "*S10E06*nfo" 2>/dev/null | head -1)
        [ -n "$NFO" ] && fix_nfo_ep_season "$NFO" 10 6
    }
done

echo ""
echo "========================================="
echo "  DONE. DRY_RUN=$DRY_RUN"
if [ "$DRY_RUN" = "0" ]; then
    echo "  Triggering Emby library rescan..."
    curl -s -X POST "https://emby.in.fukiyato.com/emby/Library/Refresh?api_key=36825b1ab6394b8daee5bc1c2186bd90"
    echo "  Rescan triggered."
fi
echo "========================================="
