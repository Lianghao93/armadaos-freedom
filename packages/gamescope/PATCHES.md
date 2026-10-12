# Patches

Patches applied on top of BASE.env. Each entry's `source` is an upstream URL pinned
to a commit, or `armada` if it's original; a URL source with no `notes` is verbatim.
`notes` mean the file was modified.

- `patches/0001-steamcompmgr-fallback-appid-focus.patch`
  source: armada
- `patches/0002-drm-synthesize-edid-for-edidless-internal-panels.patch`
  source: armada
- `patches/0003-drm-support-known-display-profiles-for-edidless-panels.patch`
  source: armada
- `patches/0004-drm-compose-gamma22-hdr-without-hardware-color-management.patch`
  source: armada
- `patches/0005-wsi-filter-hdr-formats-by-underlying-support.patch`
  source: armada
- `patches/0006-color-scale-sdr-white-on-gamma22-hdr-output.patch`
  source: armada
- `patches/0007-expose-client-sampleable-formats.patch`
  source: armada
- `patches/0009-main-add-opt-in-force-vulkan-realtime.patch`
  source: armada
- `patches/0010-color-fall-back-to-app-hdr-metadata-for-tonemapping.patch`
  source: armada
- `patches/0011-wlserver-implement-drm-lease-v1.patch`
  source: armada
- `patches/0012-wsi-layer-pass-through-display-surface-swapchains.patch`
  source: armada
- `patches/0013-feat-drm-run-a-compositor-from-the-leased-output.patch`
  source: armada
- `patches/0014-wlserver-always-swallow-ignored-touch-device.patch`
  source: armada
- `patches/0015-drm-blank-leased-connector-on-release.patch`
  source: armada
- `patches/0016-drm-let-a-socket-lease-holder-yield-to-protocol-clients.patch`
  source: armada
- `patches/0017-drm-sdr-color-management-through-dpu-output-luts.patch`
  source: armada
- `patches/0018-steamcompmgr-arm64-virtual-white.patch`
  source: armada
- `patches/0019-color-p3-red-is-wide-gamut.patch`
  source: armada
- `patches/0020-libliftoff-fix-multiple-primary-plane-stacking.patch`
  source: armada
- `patches/0021-drm-per-plane-color-management-through-msm-plane-color-pipelines.patch`
  source: armada
- `patches/0022-mangoapp-keep-a-lease-client-off-the-shared-queue.patch`
  source: armada
- `patches/0023-drm-power-down-an-idle-lease-companion-output.patch`
  source: armada
- `patches/0024-steamcompmgr-report-wayland-windows-to-steam.patch`
  source: armada
- `patches/0025-wlserver-pass-touch-through-to-native-wayland-windows.patch`
  source: armada
- `patches/0026-upscale-use-sgsr-for-sdr-fsr-requests.patch`
  source: armada
- `patches/0027-steamcompmgr-elapsed-time-fps-limiter.patch`
  source: armada

- `patches/0028-drm-collect-refresh-rates-without-edid.patch`
  source: armadaos-freedom (TB323FU adaptation)
  Empty EDID still reaches the existing device profile lookup; rates-only profiles use the kernel's modes. Unknown profiles retain real-mode fallback.

- `patches/0033-wlserver-only-take-an-edge-swipe-that-starts-at-the-edge.patch`
  source: https://github.com/armada-os/armada/blob/310c011470319aa40d264a10db855a7056842db5/packages/gamescope/patches/0028-wlserver-only-take-an-edge-swipe-that-starts-at-the-edge.patch
  PR: https://github.com/armada-os/armada/pull/665
  Original 0028 renumbered to 0033 only; body unchanged. Scope: an edge swipe must start at the edge.

- `patches/0034-edid-clamp-synthetic-pixel-clock-above-the-dtd-limit.patch`
  source: https://github.com/enij90/armada-tb321fu/commit/8d4b5530ab8afbc41d35e6577fa91e77ee872c3a
  notes: Rebased to the PR665 + Y705 tree; the two production hunks are only relocated, and the Y700 mode-list test expectation is adapted. EDID DTD metadata clamps pixel clocks above 655.35 MHz while preserving geometry; the actual scanout modes remain the kernel modes. This does NOT fix physical 165 Hz, change panel clocks, or fix the 90 Hz physical corruption. No new tests or physical fixes are claimed.
  The patch header's "not executed" statement dates from artifact generation. The unchanged patch was subsequently built in the ARM64 RPM; 17 related tests / 113 assertions passed. This sync verifies it against that RPM's source package and reuses the recorded build results. See the TB323FU status record for device acceptance and remaining issues.
