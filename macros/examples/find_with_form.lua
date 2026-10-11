-- find_with_form.lua — pick search options in a form, then step through the matches
--
-- Shows the user-interface functions: a form (opensak.form{}) whose fields
-- react to each other while it is open, remembers its values for the next
-- run, a progress window with Cancel (opensak.progress) and selecting a
-- cache in the list (opensak.select).
--
-- The form filters the cache list, looks through the matching caches for
-- the one with the most favourite points and selects it. Nothing in the
-- database is changed. Tools → Form designer in the macro window opens
-- this form in the designer when the cursor is inside it.

local values = opensak.form{
  title = "Find caches",
  remember = true,
  fields = {
    { type = "label", label = "<b>Which caches?</b>" },
    { type = "choice", id = "type", label = "Cache type",
      options = { "Traditional", "Multi-cache", "Unknown" }, default = "Traditional" },
    { type = "number", id = "dmax", label = "Max. difficulty", default = 2.5,
      min = 1, max = 5, decimals = 1, step = 0.5 },
    { type = "checkbox", id = "unfound", label = "Unfound only", default = true },
    { type = "checkbox", id = "near", label = "Near the centre point",
      on_change = function(f, on) f:set_enabled("km", on) end },
    { type = "number", id = "km", label = "Distance", default = 10, min = 1, suffix = " km" },
  },
  -- The distance only matters with "Near the centre point" ticked.
  on_open = function(f) f:set_enabled("km", f:get("near")) end,
}
if not values then return end  -- cancelled

local spec = { type = values.type, difficulty = { 1, values.dmax }, label = "Find caches" }
if values.unfound then spec.found = false end
if values.near then spec.distance = values.km end
local n = opensak.filter(spec)
if n == 0 then
  opensak.message("No caches match.", "Find caches")
  return
end

local p = opensak.progress("Looking for the favourite", n)
local best, best_points, i = nil, -1, 0
for c in opensak.caches{ fields = { "name", "favorite_points" } } do
  i = i + 1
  if not p:update(i, c.code .. " " .. c.name) then break end  -- Cancel pressed
  if (c.favorite_points or 0) > best_points then
    best, best_points = c, c.favorite_points or 0
  end
end
p:close()

if best then
  opensak.select(best.code)
  opensak.message(string.format("%d caches match. Most favourite points (%d): %s %s",
                                n, best_points, best.code, best.name), "Find caches")
end
