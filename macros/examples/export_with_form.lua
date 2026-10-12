-- export_with_form.lua — choose export options in a GSAK-style form, then export the list
--
-- Shows a form in the absolute layout, as GSAK's form designer builds it:
-- every control has its own position, grouped in frames, with the form's
-- own buttons instead of OK/Cancel. The form remembers its values for the
-- next run, a button updates a label while the form stays open, and
-- validate keeps the form open until an output folder is chosen.
--
-- Exports the caches of the active filter to the chosen folder. A folder
-- picked with the "…" button may be written for this run without a
-- folder permission.

local function count_text()
  return opensak.count() .. " caches in the list (" ..
         (opensak.filter_name() ~= "" and opensak.filter_name() or "no filter") .. ")"
end

local v, button = opensak.form{
  id = "export",
  title = "Export the cache list",
  layout = "absolute",
  width = 420,
  height = 310,
  remember = true,
  buttons = {},
  -- The form's own buttons close it, except Count: that one only updates the label.
  validate = function(f, button)
    if button == "export" and f:get("folder") == "" then
      return "Please choose an output folder."
    end
  end,
  fields = {
    { type = "label", id = "count", label = count_text(), x = 8, y = 8, w = 300 },
    { type = "button", id = "recount", label = "Count", x = 330, y = 4, w = 80, h = 25,
      on_click = function(f) f:set_label("count", count_text()) end },

    { type = "label", label = "Output folder", x = 8, y = 40 },
    { type = "folder", id = "folder", label = "Output folder", x = 8, y = 58, w = 402, h = 24 },

    { type = "group", label = "Format", x = 8, y = 92, w = 130, h = 120, fields = {
      { type = "radio", id = "format", options = { "gpx", "ggz", "kml", "loc" }, default = "gpx",
        x = 10, y = 22, w = 110, h = 90,
        -- Child waypoints and attributes exist only in GPX/GGZ (and KML).
        on_change = function(f, fmt)
          f:set_enabled("pois", fmt ~= "loc")
        end },
    } },

    { type = "group", label = "Options", x = 146, y = 92, w = 264, h = 120, fields = {
      { type = "checklist", id = "pois", options = { "Child waypoints", "Attributes" },
        default = { "Child waypoints", "Attributes" }, x = 10, y = 22, w = 244, h = 62 },
      { type = "checkbox", id = "corrected", label = "Use corrected coordinates",
        default = true, x = 10, y = 90, w = 244, h = 22 },
    } },

    { type = "checkbox", id = "dt", label = "D/T before the name", x = 8, y = 222, w = 200, h = 22,
      tooltip = "Write e.g. \"2.5/3 Cache name\" as the waypoint name" },
    { type = "label", label = "Max. caches", x = 236, y = 224 },
    { type = "number", id = "max", min = 0, max = 100000, x = 320, y = 220, w = 90, h = 24,
      tooltip = "0 = all caches" },

    { type = "separator", x = 8, y = 256, w = 402, h = 8 },
    { type = "button", id = "export", label = "Export", default = true,
      x = 244, y = 274, w = 80, h = 28 },
    { type = "button", id = "cancel", label = "Cancel", cancel = true,
      x = 330, y = 274, w = 80, h = 28 },
  },
  on_open = function(f) f:set_enabled("pois", f:get("format") ~= "loc") end,
}
if not v then return end  -- cancelled

local function has(list, item)
  for _, x in ipairs(list) do if x == item then return true end end
  return false
end

local path, n = opensak.export_gpx{
  path = v.folder .. "/{filter}." .. v.format,
  format = v.format,
  corrected = v.corrected,
  max = v.max,
  if_exists = "ask",
  pois = {
    child_waypoints = has(v.pois, "Child waypoints"),
    attributes = has(v.pois, "Attributes"),
  },
  rename = v.dt and function(c)
    return string.format("%g/%g %s", c.difficulty, c.terrain, c.name)
  end or nil,
}

if path then
  opensak.message(n .. " caches written to\n" .. path, "Export")
else
  opensak.message("Nothing was exported.", "Export")
end
