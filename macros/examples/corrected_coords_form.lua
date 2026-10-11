-- corrected_coords_form.lua — enter or remove the corrected coordinates of the selected cache
--
-- The form below was built with the form designer of the macro window
-- (Tools → Form designer) and pasted in unchanged: the designer writes the
-- opensak.form{} table, the macro supplies the functions it calls. Put the
-- cursor anywhere inside the table and open the designer again to move
-- the fields around — the callbacks (fill, show_check, check, …) are kept
-- as they are.
--
-- Select a cache in the list, then run the macro. While you type, the
-- form shows how it reads the coordinates and how far they are from the
-- posted ones; OK is refused until they can be read.

local cache = opensak.current()
if not cache then
  opensak.message("Select a cache in the list first.", "Corrected coordinates")
  return
end

-- Shows how the typed coordinates are read (on_change of "coords").
local function show_check(f, text)
  if text == "" then
    f:set_label("check", "")
    return
  end
  local lat, lon = opensak.coords.parse(text)
  if not lat then
    f:set_label("check", "✗ These coordinates cannot be read.")
    return
  end
  local km = opensak.coords.distance(cache.lat, cache.lon, lat, lon)
  f:set_label("check", string.format("✓ %s — %.2f km from the posted coordinates",
                                     opensak.coords.format(lat, lon), km))
end

-- Fills in the cache and its current corrected coordinates (on_open).
local function fill(f)
  f:set_label("cache", cache.code .. " — " .. cache.name)
  if cache.corrected then
    f:set("coords", opensak.coords.format(cache.corrected.lat, cache.corrected.lon))
    show_check(f, f:get("coords"))
  end
end

-- Keeps the form open while the coordinates cannot be read (validate).
local function check(f, button)
  if not f:get("clear") and not opensak.coords.parse(f:get("coords")) then
    return "Please enter coordinates OpenSAK can read, e.g. N47 22.123 E008 32.456."
  end
end

-- ── Built with Tools → Form designer ────────────────────────────────────────
local values, button = opensak.form{
  title = "Corrected coordinates",
  layout = "absolute",
  width = 360,
  height = 168,
  on_open = fill,
  validate = check,
  fields = {
    { type = "label", id = "cache", label = "Cache", x = 8, y = 8, w = 344, h = 16, bold = true },
    {
      type = "label",
      id = "coords_label",
      label = "Coordinates (any format)",
      x = 8,
      y = 40,
      w = 200,
      h = 16,
    },
    {
      type = "text",
      id = "coords",
      x = 8,
      y = 58,
      w = 344,
      h = 24,
      on_change = show_check,
      placeholder = "N47 22.123 E008 32.456",
    },
    { type = "label", id = "check", label = "", x = 8, y = 88, w = 344, h = 16 },
    {
      type = "checkbox",
      id = "clear",
      label = "Remove the corrected coordinates",
      x = 8,
      y = 120,
      w = 344,
      h = 20,
      on_change = function(f, on) f:set_enabled("coords", not on) end,
    },
  },
}
if not values then return end  -- cancelled

if values.clear then
  opensak.clear_corrected(cache.code)
  print(cache.code .. ": corrected coordinates removed")
else
  opensak.set_corrected(cache.code, values.coords)
  print(cache.code .. ": corrected coordinates set to " .. values.coords)
end
