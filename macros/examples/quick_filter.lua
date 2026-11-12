-- OpenSAK Macro
-- Name: Quick filter
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 5
-- Description: Filter the cache list with saved options; hold Shift to change them
-- Permissions: none
--
-- Runs unattended with the options it saved last time, so a shortcut or
-- another macro (opensak.run_macro("quick_filter")) filters the list without
-- any question. Hold Shift while starting it — Run in the Macro manager, its
-- shortcut, or Shift+F5 in the macro editor — to open the options form
-- (GSAK's $_ShiftKey). The first run opens the form too, as there is
-- nothing saved yet.
--
-- The options are kept with opensak.settings, so they belong to this macro
-- alone and survive restarts.

local s = opensak.settings.load()

if opensak.shift_key() or s.type == nil then
  local values = opensak.form{
    title = "Quick filter options",
    fields = {
      { type = "label", label = "Saved for the next runs. Hold <b>Shift</b> "
                             .. "when starting the macro to change them." },
      { type = "choice", id = "type", label = "Cache type",
        options = { "Traditional", "Multi-cache", "Unknown" },
        default = s.type or "Traditional" },
      { type = "number", id = "dmax", label = "Max. difficulty", default = s.dmax or 2.5,
        min = 1, max = 5, decimals = 1, step = 0.5 },
      { type = "checkbox", id = "unfound", label = "Unfound only",
        default = s.unfound ~= false },
    },
  }
  if not values then return end  -- cancelled: the saved options stay
  s.type, s.dmax, s.unfound = values.type, values.dmax, values.unfound
  opensak.settings.save(s)
end

local spec = { type = s.type, difficulty = { 1, s.dmax }, label = "Quick filter" }
if s.unfound then spec.found = false end
local n = opensak.filter(spec)
if n == 0 then
  print("No caches match — view unchanged. Hold Shift when starting to change the options.")
else
  print(("%d caches: %s, difficulty up to %.1f%s"):format(
    n, s.type, s.dmax, s.unfound and ", unfound" or ""))
end
