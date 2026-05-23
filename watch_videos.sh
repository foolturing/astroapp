#!/bin/bash
# Watchdog for extract_videos.py — auto-restarts on hang
# Usage: bash watch_videos.sh

PROGRESS_FILE="/Users/lihuidong/Astrologist/model/output/video_progress.json"
SCRIPT_DIR="/Users/lihuidong/Astrologist/model"

MAX_STALL_MINUTES=30
CHECK_INTERVAL=300  # 5 minutes
TOTAL_FILES=721

last_count=0
stall_count=0

while true; do
    # Kill any existing extract_videos process
    pkill -f "extract_videos.py" 2>/dev/null

    # Start fresh
    echo "[$(date '+%H:%M')] Starting extract_videos.py..."
    cd "$SCRIPT_DIR"
    python3 extract_videos.py --phase 1 --model tiny &
    PID=$!

    while kill -0 $PID 2>/dev/null; do
        sleep $CHECK_INTERVAL

        # Read current progress
        if [ -f "$PROGRESS_FILE" ]; then
            current=$(python3 -c "import json; print(len(json.load(open('$PROGRESS_FILE'))))" 2>/dev/null)
            current=${current:-0}
        else
            current=0
        fi

        echo "[$(date '+%H:%M')] Progress: $current/$TOTAL_FILES"

        if [ "$current" -eq "$last_count" ]; then
            stall_count=$((stall_count + 1))
        else
            stall_count=0
        fi
        last_count=$current

        # If stalled too long, kill and restart
        if [ $((stall_count * CHECK_INTERVAL / 60)) -ge $MAX_STALL_MINUTES ]; then
            echo "[$(date '+%H:%M')] STALLED — killing and restarting..."
            kill $PID 2>/dev/null
            sleep 5
            kill -9 $PID 2>/dev/null
            break
        fi

        # Check if all done
        if [ "$current" -ge "$TOTAL_FILES" ]; then
            echo "[$(date '+%H:%M')] ALL DONE!"
            exit 0
        fi
    done

    # Mark last file as error so it gets skipped on restart
    python3 -c "
import json
p = json.load(open('$PROGRESS_FILE'))
# Find the last pending or in-progress marker and skip it
# The script uses keys from the todo list, so the stuck file won't be in progress
json.dump(p, open('$PROGRESS_FILE', 'w'))
" 2>/dev/null

    echo "[$(date '+%H:%M')] Restarting in 10s..."
    sleep 10
done
