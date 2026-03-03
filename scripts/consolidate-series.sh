#!/bin/bash
# =============================================================================
# Emby Series Consolidation Script
# Generated 2026-03-02
# Run on Emby server: ssh barlog@192.168.1.177
# DRY_RUN=1 (default) just prints, DRY_RUN=0 executes
# =============================================================================

set -uo pipefail
DRY_RUN=${DRY_RUN:-1}

run() {
    if [ "$DRY_RUN" = "1" ]; then
        echo "[DRY] $*"
    else
        echo "[RUN] $*"
        eval "$@"
        echo "  -> exit code: $?"
    fi
}

check_exists() {
    if [ ! -e "$1" ]; then
        echo "  [SKIP] Not found: $1"
        return 1
    fi
    return 0
}

echo "========================================="
echo "  DRY_RUN=$DRY_RUN"
echo "========================================="

# ---------------------------------------------------------------
# SECTION 1: MERGE SPLIT SEASONS INTO ONE FOLDER
# ---------------------------------------------------------------
echo ""
echo "=== MERGING SPLIT SEASONS ==="

echo ""
echo "--- 1. Dragons: S01 + S02 + S03-S08 → Dragons Riders of Berk ---"
T="/Movies/Serials/--- UKONCENE ---/Dragons Riders of Berk"
check_exists "$T" && {
    check_exists "/Movies/Serials/--- UKONCENE ---/Jak vycvičit draky (2012-2014)/S02" && \
        run mv '"/Movies/Serials/--- UKONCENE ---/Jak vycvičit draky (2012-2014)/S02"' '"'"$T"'"/'
    # Third folder has nested subfolder
    S="/Movies/Serials/--- UKONCENE ---/Jak vycvičit draky - Závod na hřeben-"
    check_exists "$S" && {
        # Find season folders inside (may be nested)
        run 'find "'"$S"'" -maxdepth 2 -type d -name "S*" -exec mv {} "'"$T"'/" \; 2>/dev/null || true'
    }
    run rm -rf '"/Movies/Serials/--- UKONCENE ---/Jak vycvičit draky (2012-2014)"'
    run rm -rf '"'"$S"'"'
}

echo ""
echo "--- 2. Fargo: S01-S02 + S03-S05 → one folder ---"
T="/Movies/Serials/--- PREBIEHAJUCE ---/Fargo"
S="/Movies/Serials/--- UKONCENE ---/Fargo (2014-2024) Cz,Eng [H265]"
check_exists "$T" && check_exists "$S" && {
    for sn in S03 S04 S05; do
        check_exists "$S/$sn" && run mv '"'"$S/$sn"'"' '"'"$T"'"/'
    done
    run rm -rf '"'"$S"'"'
}

echo ""
echo "--- 3. Clarkson's Farm: S01-S02 (Serials) + S03-S04 (Dokumenty) → Dokumenty ---"
T="/Movies/Dokumenty/Clarkson's Farm (2021) - 1080p x264"
S="/Movies/Serials/--- PREBIEHAJUCE ---/Clarksonova farma"
check_exists "$T" && check_exists "$S" && {
    for sn in S01 S02; do
        check_exists "$S/$sn" && run mv '"'"$S/$sn"'"' '"'"$T"'"/'
    done
    run rm -rf '"'"$S"'"'
}

echo ""
echo "--- 4. Europe from Above: S01-S03 (720p) + S04 + S05 → one folder ---"
T="/Movies/Dokumenty/Europe From Above (2019) - 720p x264"
check_exists "$T" && {
    S4="/Movies/Dokumenty/Europe from Above S04 - 1080p WEB-DL CZ"
    S5="/Movies/Dokumenty/Europe from Above S05 - 1080p WEB-DL CZ"
    check_exists "$S4" && run mv '"'"$S4"'"' '"'"$T/S04"'"'
    check_exists "$S5" && run mv '"'"$S5"'"' '"'"$T/S05"'"'
}

echo ""
echo "--- 5. Alpha Males: merge loose into Machos Alfa ---"
T="/Movies/Serials/--- PREBIEHAJUCE ---/Machos Alfa"
S="/Movies/Serials/--- PREBIEHAJUCE ---/Alpha Males"
check_exists "$T" && check_exists "$S" && {
    run 'cp -rn "'"$S"'"/* "'"$T"'"/ 2>/dev/null || true'
    run rm -rf '"'"$S"'"'
}

echo ""
echo "--- 6. Mayfair Witches: merge S02 into Anne Rice's folder ---"
T="/Movies/Serials/--- PREBIEHAJUCE ---/Anne Rice's Mayfair Witches"
S="/Movies/Serials/--- PREBIEHAJUCE ---/Mayfair Witches"
check_exists "$S" && {
    if check_exists "$T"; then
        run 'cp -rn "'"$S"'"/* "'"$T"'"/ 2>/dev/null || true'
        run rm -rf '"'"$S"'"'
    fi
}

echo ""
echo "--- 7. Bob and Bobek: merge 1080p loose files ---"
T="/Movies/Serials/--- UKONCENE ---/Bob a Bobek - Králíci z Klobouku"
S="/Movies/Serials/--- UKONCENE ---/Bob and Bobek - 1080p x264"
check_exists "$T" && check_exists "$S" && {
    run 'cp -n "'"$S"'"/*.mkv "'"$T"'"/ 2>/dev/null || true'
    run rm -rf '"'"$S"'"'
}

echo ""
echo "--- 8. Adventurous Refurbishment: merge BluRay into main ---"
T="/Movies/Dokumenty/Eventyrlig oppussing (2015) - 1080p x264"
S="/Movies/Dokumenty/Eventyrlig oppussing S10E03-E04 - 1080p BluRay CZ"
check_exists "$T" && check_exists "$S" && {
    run 'cp -rn "'"$S"'"/* "'"$T"'"/ 2>/dev/null || true'
    run rm -rf '"'"$S"'"'
}

echo ""
echo "--- 9. Legend of Korra: merge SK into EN ---"
T="/Movies/Serials/--- UKONCENE ---/The Legend of Korra"
S="/Movies/Serials/--- UKONCENE ---/Legenda Korry"
check_exists "$T" && check_exists "$S" && {
    run 'cp -rn "'"$S"'"/* "'"$T"'"/ 2>/dev/null || true'
    run rm -rf '"'"$S"'"'
}

echo ""
echo "--- 10. Person of Interest: merge CZ into EN ---"
T="/Movies/Serials/--- UKONCENE ---/Person of Interest"
S="/Movies/Serials/--- UKONCENE ---/Lovci zlocincu"
check_exists "$T" && check_exists "$S" && {
    run 'for s in S01 S02 S04; do [ -d "'"$S"'/$s" ] && cp -rn "'"$S"'/$s/"* "'"$T"'/$s/" 2>/dev/null; done || true'
    run rm -rf '"'"$S"'"'
}

# ---------------------------------------------------------------
# SECTION 2: REMOVE DUPLICATE FOLDERS (content already in main)
# ---------------------------------------------------------------
echo ""
echo "=== REMOVING DUPLICATE FOLDERS ==="

# Castle: 1080p has all S01-S08 (133G), Vtierka only S04-S06 (12G subset)
echo "--- Castle: 1080p is complete, remove Vtierka ---"
check_exists "/Movies/Serials/--- UKONCENE ---/Vtierka Castle" && \
    run rm -rf '"/Movies/Serials/--- UKONCENE ---/Vtierka Castle"'

# Money Heist: La casa has S01-S05 (64G), Money Heist only S05 (15G subset)
echo "--- Money Heist: La casa is complete ---"
check_exists "/Movies/Serials/--- UKONCENE ---/Money Heist" && \
    run rm -rf '"/Movies/Serials/--- UKONCENE ---/Money Heist"'

# Poirot: main has S01-S13 (52G), alt is 1.1G
echo "--- Poirot: keep main 52G ---"
check_exists "/Movies/Serials/--- UKONCENE ---/Hercule Poirot - Agatha Christie's Poirot - 1989" && \
    run rm -rf '"/Movies/Serials/--- UKONCENE ---/Hercule Poirot - Agatha Christie'\''s Poirot - 1989"'

# Apocalypse WWII: 1080p (14G) vs 480p (4.2G)
echo "--- Apocalypse: keep 1080p, remove 480p ---"
check_exists "/Movies/Dokumenty/Apokalypsa 2 svetova valka (2009) - 480p" && \
    run rm -rf '"/Movies/Dokumenty/Apokalypsa 2 svetova valka (2009) - 480p"'

# PAW Patrol: CZ complete S01-S10 (290G) vs EN partial (98G)
echo "--- PAW Patrol: CZ is complete, remove EN partial ---"
check_exists "/Movies/Serials/--- PREBIEHAJUCE ---/PAW Patrol" && \
    run rm -rf '"/Movies/Serials/--- PREBIEHAJUCE ---/PAW Patrol"'

# NCIS Tony & Ziva: 4 copies, keep one
echo "--- NCIS Tony & Ziva: keep canonical, remove 3 dupes ---"
for d in \
    "/Movies/Serials/--- PREBIEHAJUCE ---/Namorni vysetrovaci sluzba: Tony a Ziva (NCIS: &amp; Ziva) -" \
    "/Movies/Serials/--- PREBIEHAJUCE ---/Namorni vysetrovaci sluzba: Tonya Ziva (NCIS: Tony &amp; Ziva) -" \
    "/Movies/Serials/--- PREBIEHAJUCE ---/NCIS Tony &amp; Ziva"; do
    check_exists "$d" && run rm -rf '"'"$d"'"'
done

# ---------------------------------------------------------------
# SECTION 3: SIMPLE DUPLICATE REMOVALS (same content, alt name)
# ---------------------------------------------------------------
echo ""
echo "=== SIMPLE DUPLICATE REMOVALS ==="

SIMPLE_REMOVES=(
    "/Movies/Serials/--- UKONCENE ---/ER"
    "/Movies/Serials/--- PREBIEHAJUCE ---/Dr Seusss Red Fish Blue"
    "/Movies/Serials/--- PREBIEHAJUCE ---/Georgie and Mandy's First Wedding"
    "/Movies/Serials/--- PREBIEHAJUCE ---/Pece cela zeme"
    "/Movies/Serials/--- PREBIEHAJUCE ---/Unos letadla"
    "/Movies/Serials/--- PREBIEHAJUCE ---/Hjem Til JulINTERNAL"
    "/Movies/Serials/--- UKONCENE ---/LEGO Star Wars Prestavajme galaxiu"
    "/Movies/Serials/--- UKONCENE ---/Love and Death (2)"
    "/Movies/Serials/--- UKONCENE ---/Marie Terezie - 1080p WEB-DL CZ"
    "/Movies/Serials/--- PREBIEHAJUCE ---/Maxton Hall Die Welt zwischen uns"
    "/Movies/Serials/--- PREBIEHAJUCE ---/Hardacreovi"
    "/Movies/Dokumenty/Fotr na tripu - CZ"
    "/Movies/Serials/--- UKONCENE ---/Last Man On The Eart"
    "/Movies/Serials/--- PREBIEHAJUCE ---/The Last Thing He Told Me"
    "/Movies/Serials/--- PREBIEHAJUCE ---/Genndy Tartakovsky Primal"
    "/Movies/Serials/--- PREBIEHAJUCE ---/Pan profesor (2)"
    "/Movies/Serials/--- PREBIEHAJUCE ---/Spartakus Ashuruv dum"
    "/Movies/Serials/--- PREBIEHAJUCE ---/Les Sentinelles"
    "/Movies/Dokumenty/Zlutou zabou az do Mongolska (2026)"
    "/Movies/Dokumenty/Mean Girl Murders (2023) - 1080p x264"
    "/Movies/Dokumenty/Outlast (2023) - 1080p x264"
)

for d in "${SIMPLE_REMOVES[@]}"; do
    check_exists "$d" && run rm -rf '"'"$d"'"'
done

# These have special chars that need careful handling
echo "--- Special char folders ---"
check_exists "/Movies/Serials/--- PREBIEHAJUCE ---/King &amp; Conqueror EN Tit CZ" && \
    run rm -rf '"/Movies/Serials/--- PREBIEHAJUCE ---/King &amp; Conqueror EN Tit CZ"'

check_exists "/Movies/Serials/--- PREBIEHAJUCE ---/Rychlá Rota (1989)" && \
    run rm -rf '"/Movies/Serials/--- PREBIEHAJUCE ---/Rychlá Rota (1989)"'

# Sullivan's Crossing - single quote in path needs special handling
run 'rm -rf /Movies/Serials/---\ PREBIEHAJUCE\ ---/Návrat\ do\ Sullivan'\''s\ Crossing\ \(S01-S02\)\(2023\)'

# Walking with Dinosaurs - path has truncated name
for d in "/Movies/Serials/--- PREBIEHAJUCE ---/Putovani"*; do
    [ -d "$d" ] && echo "Found: $d" && run rm -rf '"'"$d"'"'
done

# ---------------------------------------------------------------
# SECTION 4: SKIP (needs manual check)
# ---------------------------------------------------------------
echo ""
echo "=== SKIPPED (manual check needed) ==="
echo "  Cosmos (2014) vs Spacetime (2016) — may be different shows"

# ---------------------------------------------------------------
# TRIGGER EMBY RESCAN
# ---------------------------------------------------------------
echo ""
if [ "$DRY_RUN" = "0" ]; then
    echo "Triggering Emby library rescan..."
    curl -s -X POST "https://emby.in.fukiyato.com/emby/Library/Refresh?api_key=36825b1ab6394b8daee5bc1c2186bd90" && echo "  -> Rescan triggered"
else
    echo "[DRY] Would trigger Emby library rescan"
fi

echo ""
echo "========================================="
echo "  DONE. DRY_RUN=$DRY_RUN"
echo "========================================="
