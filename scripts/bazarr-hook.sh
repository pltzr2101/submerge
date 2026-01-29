#!/bin/sh
# Bazarr post-processing hook for SubMerge
#
# This script is called by Bazarr after downloading a subtitle.
# It sends the subtitle to SubMerge for bilingual merging.
#
# Arguments passed by Bazarr:
#   $1 = video file path
#   $2 = subtitle file path
#   $3 = subtitle language (ISO 639-1 code)
#
# Usage in Bazarr:
#   Settings > Subtitles > Post-Processing
#   Command: /path/to/bazarr-hook.sh "{{episode}}" "{{subtitles}}" "{{subtitles_language_code2}}"
#
# Note: The quotes around variables are REQUIRED to handle paths with special characters.

# SubMerge API endpoint (adjust if needed)
SUBMERGE_URL="${SUBMERGE_URL:-http://submerge:8282/hook}"

curl -sf -X POST "$SUBMERGE_URL" \
  --data-urlencode "video=$1" \
  --data-urlencode "subtitle=$2" \
  --data-urlencode "lang=$3"
