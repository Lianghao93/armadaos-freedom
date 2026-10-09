-- Lenovo Legion Y700 Gen 5 (TB323FU) internal LCD panel.
-- Uses the kernel's real modes as-is; no new timings are generated here.

gamescope.config.known_displays.armada_lenovo_tb323fu_lcd = {
	pretty_name = "Lenovo Legion Y700 Gen 5 (TB323FU)",
	dynamic_refresh_rates = { 30, 60, 90, 120, 164 },
	matches = function(display)
		return display.device_id == "lenovo-tb323fu"
			and display.internal
			and display.connector == "DSI-1"
			and 6000 or -1
	end,
}

-- TB323FU fixed-refresh composite-path runtime convars. The device is
-- identified through GAMESCOPE_INTERNAL_DEVICE_ID at load time; the convars
-- only exist in builds that carry the TB323FU composite work, so every lookup
-- is nil-guarded for older binaries. Setting an absent convar is a no-op.
local function set_tb323fu_convar(name, value)
	if gamescope == nil or gamescope.convars == nil then
		return
	end

	local cv = gamescope.convars[name]
	if cv ~= nil then
		cv.value = value
	end
end

if os ~= nil and os.getenv ~= nil
	and os.getenv("GAMESCOPE_INTERNAL_DEVICE_ID") == "lenovo-tb323fu" then
	set_tb323fu_convar("y700_late_base_paint", true)
	set_tb323fu_convar("y700_use_paint_work_budget", true)
	set_tb323fu_convar("y700_keep_preemptive_cycle", false)
	set_tb323fu_convar("y700_phase_debug", false)
	set_tb323fu_convar("y700_present_debug", false)
end
