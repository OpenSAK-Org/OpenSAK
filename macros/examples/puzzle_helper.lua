-- OpenSAK Macro
-- Name: Puzzle helper
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 3
-- Description: Coordinates, formulas and numbers from a mystery listing; ROT13, digit sum, word value
--
-- Select a cache in the list, then run the macro. It reads the listing
-- and opens one window; every button shows its result in the box in the
-- middle, and the window stays open until you close it:
--
--   * In the listing: the coordinates written in the listing, also those
--     hidden in HTML comments, tag attributes or scripts, and formulas
--     such as "N47 2A.BCD E008 3E.FGH". For a formula, type the letters
--     ("A=3, B=12, C=word": a word counts as its letter value A=1 … Z=26)
--     and press Solve.
--   * Tools: digit sum, word value and ROT13 of what you type, the numbers
--     in the listing, the decoded hint and the listing as plain text.
--   * Save as waypoint: Show and Solve fill in the coordinates; you can
--     also type your own. Changing a cache needs your approval, which
--     OpenSAK asks for the first time.

local TITLE = "Puzzle helper"

local cache = opensak.current()
if not cache then
  opensak.message("Select a cache in the list first.", TITLE)
  return
end

-- ── The listing as plain text ────────────────────────────────────────────────
local desc = opensak.description(cache.code) or {}
local listing = (desc.short or "") .. "\n" .. (desc.long or "")
if desc.html then listing = opensak.text.html_to_text(listing) end

-- One pattern for coordinates and formulas: hemisphere, degrees and minutes,
-- where any digit may be a letter. "O" (Ost) is accepted for East and a
-- decimal comma for the point. The four captures are the two hemispheres
-- and the two numbers.
local COORDS = [[(?<![A-Za-z])([NS])\s*([\dA-Z]{1,2}°?\s*[\dA-Z]{1,2}[.,][\dA-Z]{3})'?]]
            .. [[\s*([EWO])\s*([\dA-Z]{1,3}°?\s*[\dA-Z]{1,2}[.,][\dA-Z]{3})'?]]

-- Puts hemispheres and numbers back together the way coords.parse reads them.
local function join(ns, lat, ew, lon)
  if ew == "O" then ew = "E" end
  return opensak.re.replace(ns .. " " .. lat .. " " .. ew .. " " .. lon, ",", ".")
end

-- found / formulas: the menu labels, each the coordinates plus where they
-- are hidden, if they are; coords_of and parts: the coordinates and the four
-- parts of a formula by label. Each coordinate is listed once.
local found, formulas, coords_of, parts, listed = {}, {}, {}, {}, {}
local function scan(text, where)
  for _, m in ipairs(opensak.re.findall(text, COORDS)) do
    local coords = join(m[1], m[2], m[3], m[4])
    local label = where and coords .. "  (" .. where .. ")" or coords
    if listed[coords] then
      -- listed already
    elseif opensak.re.match(m[2] .. m[4], "[A-Z]") then
      formulas[#formulas + 1] = label
      parts[label] = m
    elseif opensak.coords.parse(coords) then
      found[#found + 1] = label
      coords_of[label] = coords
    end
    listed[coords] = true
  end
end

-- The visible text first. White text and text hidden with CSS
-- (display:none) are part of it: html_to_text keeps all text.
scan(listing)

-- Then what html_to_text leaves out: HTML comments, attribute values
-- (image alt/title, link URLs, file names) and <script> / <style> blocks.
if desc.html then
  local raw = (desc.short or "") .. "\n" .. (desc.long or "")
  for _, c in ipairs(opensak.re.findall(raw, [[(?s)<!--(.*?)-->]])) do
    scan(tostring(c), "in an HTML comment")
  end
  local ATTRIBUTE = [[(?i)\s(alt|title|href|src|value|data-[\w-]+)\s*=\s*["']([^"'<>]*)]]
  for _, a in ipairs(opensak.re.findall(raw, ATTRIBUTE)) do
    -- URLs write spaces, commas and ° encoded
    local value = opensak.re.replace(a[2], [[%20|\+]], " ")
    value = opensak.re.replace(value, "(?i)%C2%B0", "°")
    value = opensak.re.replace(value, "(?i)%2C", ",")
    scan(value, "in the " .. string.lower(a[1]) .. " of a tag")
  end
  for _, b in ipairs(opensak.re.findall(raw, [[(?is)<(script|style)\b[^>]*>(.*?)</\1>]])) do
    scan(b[2], "in a <" .. string.lower(b[1]) .. "> block")
  end
end

-- ── Working with points and formulas ─────────────────────────────────────────
local PREFIX = { ["Final Location"] = "FN", ["Reference Point"] = "RP",
                 ["Stages of a Multicache"] = "S", ["Question to Answer"] = "QA" }
local TYPES = { "Final Location", "Reference Point", "Stages of a Multicache",
                "Question to Answer" }

-- The first prefix of this type the cache does not use yet: FN, FN1, FN2 …
local function free_prefix(base)
  local used = {}
  for _, w in ipairs(opensak.waypoints(cache.code) or {}) do used[w.prefix] = true end
  if base ~= "S" and not used[base] then return base end
  for n = 1, 99 do
    if not used[base .. n] then return base .. n end
  end
end

-- A point in three formats, with distance and direction from the posted coordinates.
local function describe(lat, lon)
  local km = opensak.coords.distance(cache.lat, cache.lon, lat, lon)
  local lines = {
    opensak.coords.format(lat, lon),
    opensak.coords.format(lat, lon, "dms"),
    opensak.coords.format(lat, lon, "dd"),
    "",
    string.format("%.2f km from the posted coordinates, bearing %.0f°",
                  km, opensak.coords.bearing(cache.lat, cache.lon, lat, lon)),
  }
  if km > 3.22 then
    lines[#lines + 1] = "More than 2 miles away: a mystery final is usually closer."
  end
  return table.concat(lines, "\n")
end

-- The letters to solve in a formula, in order of appearance, each once;
-- the hemispheres are not letters to solve.
local function letters_of(formula)
  local _, lat, _, lon = table.unpack(parts[formula])
  local list, seen = {}, {}
  for _, m in ipairs(opensak.re.findall(lat .. lon, "[A-Z]")) do
    local l = tostring(m)
    if not seen[l] then
      seen[l] = true
      list[#list + 1] = l
    end
  end
  return list
end

-- "A=3, b = 12, C=word" → { A = "3", B = "12", C = "word" }
local function read_letters(text)
  local values = {}
  for _, m in ipairs(opensak.re.findall(text, [[([A-Za-z])\s*=\s*([^,;\n]*)]])) do
    values[string.upper(m[1])] = (opensak.re.replace(m[2], [[^\s+|\s+$]], ""))
  end
  return values
end

-- "A=, B=, …" for a formula, keeping the values typed for letters it shares.
local function letters_template(formula, typed)
  local values, out = read_letters(typed or ""), {}
  for _, l in ipairs(letters_of(formula)) do out[#out + 1] = l .. "=" .. (values[l] or "") end
  return table.concat(out, ", ")
end

-- A number stays a number; anything else counts as its word value, and
-- the second value says so.
local function value_of(answer)
  if opensak.re.match(answer, [[^\d+$]]) then return answer end
  local v = opensak.text.word_value(answer)
  return tostring(v), "word value of " .. answer
end

-- ── What the buttons do ──────────────────────────────────────────────────────

-- Where the coordinates in the Save box came from, for the waypoint's comment.
local source = { coords = nil, text = nil }

local function show(f, heading, text)
  f:set("output", "── " .. heading .. " ──\n\n" .. text)
end

local function propose(f, lat, lon, from)
  local coords = opensak.coords.format(lat, lon)
  f:set("result", coords)
  source.coords, source.text = coords, from
end

local function show_found(f)
  local label = tostring(f:get("found"))
  local coords = coords_of[label]
  if not coords then return end
  local lat, lon = opensak.coords.parse(coords)
  propose(f, lat, lon, "from the listing: " .. label)
  show(f, "From the listing", label .. "\n\n" .. describe(lat, lon))
end

local function solve(f)
  local formula = tostring(f:get("formula"))
  if not parts[formula] then return end
  local ns, lat, ew, lon = table.unpack(parts[formula])
  local typed = read_letters(f:get("letters"))
  local values, lines, missing = {}, {}, {}
  for _, l in ipairs(letters_of(formula)) do
    if not typed[l] or typed[l] == "" then
      missing[#missing + 1] = l
    else
      local v, how = value_of(typed[l])
      values[l] = v
      lines[#lines + 1] = l .. " = " .. v .. (how and "   (" .. how .. ")" or "")
    end
  end
  if #missing > 0 then
    show(f, "Formula", formula .. "\n\nStill missing: " .. table.concat(missing, ", "))
    return
  end
  local fill = function(letter) return values[letter] end
  local text = join(ns, (opensak.re.replace(lat, "[A-Z]", fill)), ew,
                    (opensak.re.replace(lon, "[A-Z]", fill)))
  local body = formula .. "\n" .. table.concat(lines, "\n") .. "\n\n= " .. text
  local la, lo = opensak.coords.parse(text)
  if not la then
    show(f, "Formula", body .. "\n\nThese are not valid coordinates. Check the values.")
    return
  end
  local used = {}
  for _, l in ipairs(letters_of(formula)) do used[#used + 1] = l .. "=" .. values[l] end
  propose(f, la, lo, formula .. " with " .. table.concat(used, ", "))
  show(f, "Formula", body .. "\n\n" .. describe(la, lo))
end

-- Digit sum, repeated down to one digit (the digital root).
local function digit_sum(f)
  local text = f:get("input")
  local n = opensak.text.digit_sum(text)
  local steps = { tostring(n) }
  while n >= 10 do
    n = opensak.text.digit_sum(n)
    steps[#steps + 1] = tostring(n)
  end
  show(f, "Digit sum", text .. "\n\nDigit sum: " .. steps[1]
       .. (#steps > 1 and "\nRepeated:  " .. table.concat(steps, " → ") or ""))
end

-- Word value of each word, and of the whole text.
local function word_value(f)
  local lines, total = {}, 0
  for _, word in ipairs(opensak.re.split(f:get("input"), [[[^\w]+]])) do
    if word ~= "" then
      local v = opensak.text.word_value(word)
      lines[#lines + 1] = string.format("%-20s %4d", word, v)
      total = total + v
    end
  end
  lines[#lines + 1] = string.format("%-20s %4d   (digit sum %d)", "Total", total,
                                    opensak.text.digit_sum(total))
  show(f, "Word value", table.concat(lines, "\n"))
end

local function rot13(f)
  show(f, "ROT13", opensak.text.rot13(f:get("input")))
end

local function numbers(f)
  local seen, lines = {}, {}
  for _, m in ipairs(opensak.re.findall(listing, [[\d+]])) do
    local n = tostring(m)   -- one item per match: no capture groups
    if not seen[n] then
      seen[n] = true
      lines[#lines + 1] = string.format("%12s   digit sum %d", n, opensak.text.digit_sum(n))
    end
  end
  show(f, "Numbers in the listing (" .. #lines .. ")",
       #lines > 0 and table.concat(lines, "\n") or "The listing contains no numbers.")
end

local function hint(f)
  if cache.hint and cache.hint ~= "" then
    show(f, "Hint", opensak.text.rot13(cache.hint))
  else
    show(f, "Hint", "This cache has no hint.")
  end
end

local function listing_text(f)
  show(f, "Listing as plain text", listing)
end

local function save(f)
  local typed = f:get("result")
  local lat, lon = opensak.coords.parse(typed)
  if not lat then
    show(f, "Save as waypoint", "Put coordinates OpenSAK can read into the Coordinates field first.")
    return
  end
  if not opensak.insert_waypoint then   -- writing caches is a beta feature
    show(f, "Save as waypoint", "Saving waypoints needs a beta build of OpenSAK.")
    return
  end
  local wtype = tostring(f:get("wptype"))
  local name = f:get("wpname") ~= "" and f:get("wpname") or wtype
  local from = typed == source.coords and source.text or "entered by hand"
  local prefix = free_prefix(PREFIX[wtype])
  opensak.insert_waypoint(cache.code, { prefix = prefix, type = wtype, name = name,
                                        lat = lat, lon = lon, comment = "Puzzle helper: " .. from })
  local text = string.format("%s: waypoint %s (%s) saved at %s",
                             cache.code, prefix, wtype, opensak.coords.format(lat, lon))
  print(text)
  show(f, "Save as waypoint", text .. "\n" .. from)
end

-- ── The window ───────────────────────────────────────────────────────────────
local function plural(n, word) return n .. " " .. word .. (n == 1 and "" or "s") end

local intro = plural(#found, "coordinate") .. " and " .. plural(#formulas, "formula")
              .. " found in the listing."
local checker = opensak.re.find(listing, [[(?i)geocheck(?:er)?|certitude|checker]])
if checker then
  intro = intro .. "\nThe listing mentions " .. checker .. ": test your result there."
end
intro = intro .. "\n\nPick coordinates or a formula above, or type a text or number"
              .. " and use the tools."

local NONE = { "(none found)" }

opensak.form{
  title = TITLE .. " — " .. cache.code,
  layout = "absolute",
  width = 620,
  height = 600,
  buttons = { { id = "close", label = "Close", cancel = true } },
  fields = {
    { type = "label", id = "cache", label = cache.code .. " — " .. cache.name,
      x = 8, y = 8, w = 604, h = 18, bold = true },
    {
      type = "group", label = "In the listing", x = 8, y = 32, w = 604, h = 150,
      fields = {
        { type = "label", label = "Coordinates", x = 10, y = 26, w = 80, h = 20 },
        { type = "choice", id = "found", options = #found > 0 and found or NONE,
          enabled = #found > 0, x = 95, y = 22, w = 390, h = 24 },
        { type = "button", id = "show", label = "Show", enabled = #found > 0,
          x = 492, y = 21, w = 100, h = 26, on_click = show_found },
        { type = "label", label = "Formula", x = 10, y = 60, w = 80, h = 20 },
        { type = "choice", id = "formula", options = #formulas > 0 and formulas or NONE,
          enabled = #formulas > 0, x = 95, y = 56, w = 390, h = 24,
          on_change = function(f, value)
            f:set("letters", letters_template(tostring(value), f:get("letters")))
          end },
        { type = "button", id = "solve", label = "Solve", enabled = #formulas > 0,
          x = 492, y = 55, w = 100, h = 26, on_click = solve },
        { type = "label", label = "Letters", x = 10, y = 94, w = 80, h = 20 },
        { type = "text", id = "letters", enabled = #formulas > 0, remember = false,
          default = #formulas > 0 and letters_template(formulas[1]) or "",
          placeholder = "A=3, B=12, C=word", x = 95, y = 90, w = 390, h = 24 },
        { type = "label", label = "Each letter: a number, or a word for its letter value (A=1 … Z=26).",
          italic = true, x = 95, y = 118, w = 497, h = 18 },
      },
    },
    {
      type = "group", label = "Tools", x = 8, y = 188, w = 604, h = 96,
      fields = {
        { type = "text", id = "input", x = 10, y = 24, w = 582, h = 24,
          placeholder = "Text or number for Digit sum, Word value and ROT13" },
        { type = "button", id = "digit", label = "Digit sum", x = 10, y = 58, w = 90, h = 26,
          on_click = digit_sum },
        { type = "button", id = "word", label = "Word value", x = 104, y = 58, w = 90, h = 26,
          on_click = word_value },
        { type = "button", id = "rot13", label = "ROT13", x = 198, y = 58, w = 70, h = 26,
          on_click = rot13 },
        { type = "button", id = "numbers", label = "Numbers in listing", x = 272, y = 58, w = 130,
          h = 26, on_click = numbers },
        { type = "button", id = "hint", label = "Decode hint", x = 406, y = 58, w = 90, h = 26,
          on_click = hint },
        { type = "button", id = "listing", label = "Listing text", x = 500, y = 58, w = 92, h = 26,
          on_click = listing_text },
      },
    },
    { type = "memo", id = "output", default = intro, readonly = true, monospace = true,
      x = 8, y = 292, w = 604, h = 200 },
    {
      type = "group", label = "Save as waypoint", x = 8, y = 500, w = 604, h = 92,
      fields = {
        { type = "label", label = "Coordinates", x = 10, y = 26, w = 80, h = 20 },
        { type = "text", id = "result", placeholder = "N47 22.123 E008 32.456",
          x = 95, y = 22, w = 497, h = 24 },
        { type = "label", label = "Type", x = 10, y = 60, w = 80, h = 20 },
        { type = "choice", id = "wptype", options = TYPES, x = 95, y = 56, w = 200, h = 24 },
        { type = "text", id = "wpname", placeholder = "Name (default: the type)",
          x = 300, y = 56, w = 186, h = 24 },
        { type = "button", id = "save", label = "Save", x = 492, y = 55, w = 100, h = 26,
          on_click = save },
      },
    },
  },
}
