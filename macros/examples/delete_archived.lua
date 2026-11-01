-- OpenSAK Macro
-- Name: Delete archived caches
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 5
-- Description: Delete the archived caches you haven't found from the active database
-- Permissions: write
-- Headless: yes
--
-- Keeps a "not found" database clean, e.g. after importing Pocket Queries.
-- Caches you found or own are kept, and so are locked ones. The active filter
-- doesn't matter and stays as it is.
--
-- In OpenSAK it asks first. Without a window it doesn't: being allowed to
-- write is the decision. Deleted caches only come back from a backup.
--
--   opensak db backup "Not found"
--   opensak run delete_archived.lua --db "Not found" --allow-write
--
-- To only list what it would delete, add --arg dry_run=yes.

local DRY_RUN = opensak.args.dry_run == "yes"

local codes = {}
for c in opensak.caches{ archived = true, found = false, owned = false, locked = false,
                         fields = { "code", "name" } } do
    codes[#codes + 1] = c.code
    if DRY_RUN then
        print(c.code, c.name)
    end
end

if #codes == 0 then
    print("No archived caches to delete")
    return
end
if DRY_RUN then
    print(#codes .. " archived caches would be deleted")
    return
end
if not opensak.is_headless()
        and not opensak.confirm("Delete " .. #codes .. " archived caches?") then
    print("Nothing deleted")
    return
end

print(opensak.delete(codes) .. " archived caches deleted")
