#!/bin/bash
# capture-ui-sync-trace.sh - bounded 30s ftrace capture on TB323FU.
# Read-only diagnostics: does NOT change CPU/GPU freq/cores/thermal/display,
# global tracing, or any existing steamos-log-submitter instance.
set -u

OUT=/var/home/armada/.cache/y700-debug-90781b49/ui-sync-trace-r1
INST=/sys/kernel/tracing/instances/y700_ui_90781b49
CLEANED=0

die(){ echo "ERROR: $*" >&2; exit 1; }

idle_state(){
  local f=/sys/module/msm/parameters/idle_refresh_state
  [ -f "$f" ] && { echo "$f=$(cat "$f" 2>/dev/null)"; return; }
  echo unavailable
}

meta(){
  { echo "[$1] date=$(date -Ins)"
    echo "[$1] uptime=$(cat /proc/uptime)"
    echo "[$1] boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)"
    echo "[$1] idle_refresh_state=$(idle_state)"
  } >> "$OUT/meta.txt"
}

cleanup(){
  [ "$CLEANED" = 1 ] && return 0
  CLEANED=1
  [ -d "$INST" ] || return 0
  echo 0 > "$INST/tracing_on" 2>/dev/null || true          # own instance only
  mkdir -p "$OUT/output" 2>/dev/null || true
  cat "$INST/trace" > "$OUT/output/trace.txt" 2>/dev/null || true
  : > "$OUT/stats.txt" 2>/dev/null || true
  local s
  for s in "$INST"/per_cpu/cpu*/stats; do
    [ -f "$s" ] || continue
    { echo "== $s =="; cat "$s" 2>/dev/null; } >> "$OUT/stats.txt" || true
  done
  rmdir "$INST" 2>/dev/null || true                         # no rm -rf
}

EVENTS=(
  drm_msm_gpu/msm_gpu_submit drm_msm_gpu/msm_gpu_submit_flush drm_msm_gpu/msm_gpu_submit_retired
  drm_msm_gpu/msm_gpu_preemption_trigger drm_msm_gpu/msm_gpu_preemption_irq
  drm_msm_atomic/msm_atomic_commit_tail_start drm_msm_atomic/msm_atomic_commit_tail_finish
  drm_msm_atomic/msm_atomic_wait_flush_start drm_msm_atomic/msm_atomic_wait_flush_finish
  drm_msm_atomic/msm_atomic_flush_commit
  dma_fence/dma_fence_wait_start dma_fence/dma_fence_wait_end dma_fence/dma_fence_signaled
  drm/drm_vblank_event_delivered
  dpu/dpu_crtc_vblank_cb dpu/dpu_enc_trigger_flush dpu/dpu_enc_underrun_cb dpu/dpu_enc_wait_event_timeout
)
REQUIRED=(drm_msm_gpu/msm_gpu_submit drm_msm_gpu/msm_gpu_submit_retired drm/drm_vblank_event_delivered)

[ -d /sys/kernel/tracing/instances ] || die "tracefs not available"
[ -e "$INST" ] && die "instance already exists: $INST (leaving it untouched)"
mkdir -p "$OUT" || die "cannot create $OUT"
mkdir "$INST" || die "cannot create $INST"
on_int(){ exit 130; }                                       # EXIT trap does cleanup
on_term(){ exit 143; }
trap cleanup EXIT
trap on_int INT
trap on_term TERM

echo 1024 > "$INST/buffer_size_kb" || die "cannot set buffer_size_kb"
echo mono > "$INST/trace_clock" || die "cannot set trace_clock=mono"

: > "$OUT/events.txt"
for ev in "${EVENTS[@]}"; do
  if [ -e "$INST/events/$ev/enable" ] && echo 1 > "$INST/events/$ev/enable" 2>/dev/null; then
    echo "enabled $ev" >> "$OUT/events.txt"
  else
    echo "skip $ev" >> "$OUT/events.txt"
  fi
done
for ev in "${REQUIRED[@]}"; do
  e="$INST/events/$ev/enable"
  [ -e "$e" ] || die "required event missing: $ev"
  [ "$(cat "$e" 2>/dev/null)" = 1 ] || die "required event not enabled: $ev"
done

meta start
echo 1 > "$INST/tracing_on" || die "cannot start tracing"
echo BEGIN_UI_SYNC_DIAG > "$INST/trace_marker" 2>/dev/null || true
echo READY
sleep 30
echo END_UI_SYNC_DIAG > "$INST/trace_marker" 2>/dev/null || true
meta end
exit 0
