-- Tessera Wall shape probe (run by probe.py, not a user script).
-- mpv opens each playlist entry paused with no video or audio output; as soon as a file is loaded
-- its first real video track's size, rotation and pixel shape (from the container, no decoding) are
-- appended to the file named by --script-opts=tw-out=<path>, and the next entry is opened.
-- Line format, tab-separated: playlist index, width, height, rotation (degrees), pixel aspect.
-- A file that fails to open writes no line.
local out = io.open(mp.get_opt("tw-out"), "w")

mp.register_event("file-loaded", function()
    local index = mp.get_property_number("playlist-pos", -1)
    local w, h, rot, par = 0, 0, 0, 1
    for _, t in ipairs(mp.get_property_native("track-list") or {}) do
        if t.type == "video" and not t.albumart and not t.image then
            w, h = t["demux-w"] or 0, t["demux-h"] or 0
            rot = t["demux-rotation"] or 0
            par = t["demux-par"] or 1
            break
        end
    end
    out:write(string.format("%d\t%d\t%d\t%d\t%.6f\n", index, w, h, rot, par))
    out:flush()
    mp.command("playlist-next force")
end)

mp.register_event("shutdown", function()
    out:close()
end)
