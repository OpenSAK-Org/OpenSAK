# tests/screenshots/demo_data.py — curated demo dataset for marketing/doc screenshots.
#
# Unlike tests/data.py (minimal fixtures for unit/e2e assertions), this dataset is
# deliberately varied and "nice looking" — it exists purely so that screenshots of
# the app (website, README, User Guide) show a realistic, populated cache list
# instead of 2-4 bare test caches. Do not use this in unit/e2e tests; use
# tests.data.seed_standard_caches there instead.

from __future__ import annotations

from pathlib import Path

from tests.data import build_gpx, cache_wpt, write_gpx


def _rot13(text: str) -> str:
    return text.translate(
        str.maketrans(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
            "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm",
        )
    )


# Each entry maps 1:1 onto tests.data.cache_wpt() kwargs (gc_code is positional).
# Coordinates are spread around Zealand, Denmark, with a couple further out so the
# map view doesn't look like a single tight cluster.
DEMO_CACHES: list[dict] = [
    dict(
        gc_code="GC1A001", name="Viking Ship Lookout", lat=55.6761, lon=12.5683,
        cache_type="Traditional Cache", container="Small", difficulty=1.5, terrain=2.0,
        placed_by="AB_Green", gs_id=100001,
        hint="Behind the information board",
        logs=[
            dict(type="Found it", finder="Geomads", finder_id=201,
                 date="2026-05-02T08:00:00Z", text="Quick lunchtime find, TFTC!"),
            dict(type="Found it", finder="TrailBlazerDK", finder_id=202,
                 date="2026-04-18T14:00:00Z", text="Nice spot by the harbour."),
        ],
    ),
    dict(
        gc_code="GC1A002", name="The Hollow Oak", lat=55.7012, lon=12.4501,
        cache_type="Traditional Cache", container="Micro", difficulty=2.0, terrain=2.5,
        placed_by="SkovTrold", gs_id=100002,
        hint=_rot13("Look under the loose bark, not inside the trunk"),
        logs=[
            dict(type="Didn't find it", finder="Geomads", finder_id=201,
                 date="2026-05-10T09:30:00Z", text="Searched for 20 min, no luck."),
        ],
    ),
    dict(
        gc_code="GC1A003", name="Castle Ruins Multi", lat=55.6432, lon=11.8901,
        cache_type="Multi-cache", container="Regular", difficulty=3.0, terrain=3.5,
        placed_by="HistorieJaeger", gs_id=100003,
        logs=[
            dict(type="Found it", finder="TrailBlazerDK", finder_id=202,
                 date="2026-03-22T11:00:00Z", text="Great series of stages, loved stage 3!"),
        ],
    ),
    dict(
        gc_code="GC1A004", name="Whispering Mill", lat=55.5012, lon=11.9355,
        cache_type="Traditional Cache", container="Large", difficulty=1.0, terrain=1.5,
        placed_by="MoelleManden", gs_id=100004,
        hint="In the old grain hopper, mind the cobwebs",
        logs=[
            dict(type="Found it", finder="AB_Green", finder_id=999,
                 date="2026-06-01T07:45:00Z", text="First to find! What a fun hide."),
        ],
    ),
    dict(
        gc_code="GC1A005", name="Coastal Mystery", lat=55.8312, lon=12.0987,
        cache_type="Unknown Cache", container="Small", difficulty=4.5, terrain=2.0,
        placed_by="Puslespil", gs_id=100005,
        hint="Solve the puzzle first — final is not at the listed coordinates",
    ),
    dict(
        gc_code="GC1A006", name="Beneath the Lighthouse", lat=55.9871, lon=11.9123,
        cache_type="Traditional Cache", container="Micro", difficulty=2.5, terrain=4.0,
        placed_by="KystVagt", gs_id=100006,
        hint=_rot13("Magnetic - check under the handrail near the steps"),
        logs=[
            dict(type="Found it", finder="Geomads", finder_id=201,
                 date="2026-04-30T16:20:00Z", text="Tricky terrain but worth it for the view."),
        ],
    ),
    dict(
        gc_code="GC1A007", name="Glacial Boulder Lesson", lat=55.4123, lon=11.7765,
        cache_type="Earthcache", container="Other", difficulty=2.0, terrain=1.5,
        placed_by="GeoLaerer", gs_id=100007,
        logs=[
            dict(type="Found it", finder="TrailBlazerDK", finder_id=202,
                 date="2026-02-14T10:00:00Z", text="Learned a lot about glacial erratics here."),
        ],
    ),
    dict(
        gc_code="GC1A008", name="The Smuggler's Letterbox", lat=55.7345, lon=12.6788,
        cache_type="Letterbox Hybrid", container="Regular", difficulty=2.5, terrain=2.0,
        placed_by="KystVagt", gs_id=100008,
        hint="Bring your own stamp ink",
    ),
    dict(
        gc_code="GC1A009", name="Wherigo: The Watchman's Round", lat=55.6601, lon=12.3344,
        cache_type="Wherigo Cache", container="Not chosen", difficulty=3.5, terrain=2.5,
        placed_by="HistorieJaeger", gs_id=100009,
    ),
    dict(
        gc_code="GC1A010", name="Harbourview Virtual", lat=55.6890, lon=12.5912,
        cache_type="Virtual Cache", container="Not chosen", difficulty=1.5, terrain=1.0,
        placed_by="AB_Green", gs_id=100010,
        logs=[
            dict(type="Found it", finder="AB_Green", finder_id=999,
                 date="2026-05-20T18:00:00Z", text="Beautiful sunset view, ftf!"),
        ],
    ),
    dict(
        gc_code="GC1A011", name="Old Quarry Traverse", lat=55.5678, lon=11.6543,
        cache_type="Traditional Cache", container="Small", difficulty=3.0, terrain=4.5,
        placed_by="MoelleManden", gs_id=100011,
        hint="Steep approach from the north side only",
    ),
    dict(
        gc_code="GC1A012", name="Parkbænken's Secret", lat=55.7201, lon=12.4789,
        cache_type="Traditional Cache", container="Nano", difficulty=1.5, terrain=1.0,
        placed_by="SkovTrold", gs_id=100012,
        hint="Magnetic, under the third bench from the gate",
        logs=[
            dict(type="Found it", finder="Geomads", finder_id=201,
                 date="2026-06-10T12:00:00Z", text="Cute little nano, blended in perfectly."),
        ],
    ),
]


def build_demo_gpx() -> str:
    # log "id" defaults to a per-cache counter (1, 2, ...) in cache_wpt(), but the
    # DB's log_id column has a UNIQUE constraint — so every log across the whole
    # dataset needs a globally distinct id, not just a per-cache one.
    next_log_id = 600001
    blocks = []
    for c in DEMO_CACHES:
        kwargs = {k: v for k, v in c.items() if k not in ("gc_code", "logs")}
        logs = c.get("logs")
        if logs:
            numbered_logs = []
            for lg in logs:
                lg = dict(lg, id=next_log_id)
                next_log_id += 1
                numbered_logs.append(lg)
            kwargs["logs"] = numbered_logs
        blocks.append(cache_wpt(c["gc_code"], **kwargs))
    return build_gpx(*blocks)


DESCRIPTIONS: dict[str, str] = {
    "GC1A001": "A short walk along the harbour with a great view of an old Viking ship replica. Family-friendly and stroller-accessible most of the year.",
    "GC1A002": "Tucked away in a small patch of forest. The oak itself is a local landmark, said to be over 200 years old.",
    "GC1A003": "A multi-stage cache through the ruins of a medieval castle. Bring a flashlight for stage 2 — it's indoors and dark.",
    "GC1A004": "An old watermill, no longer in use, now a quiet spot for a picnic. The cache is hidden somewhere in the mechanism itself.",
    "GC1A005": "A classic 'final is not where you think' mystery. Solve the puzzle on the cache page before heading out — the posted coordinates are just the puzzle start.",
    "GC1A006": "Right at the foot of a working lighthouse. Spectacular at sunset, but mind the tide times listed on the cache page.",
    "GC1A007": "An EarthCache about glacial erratics left behind by the last ice age. Answer the three questions on the cache page to log a find.",
    "GC1A008": "A letterbox hybrid with a small rubber stamp inside. Please bring your own ink pad and don't forget to swap travel items.",
    "GC1A009": "A Wherigo adventure following a night watchman's old patrol route through the old town. Best played in daylight despite the theme.",
    "GC1A010": "A virtual cache — no container, just the view. Log your find with a photo from the marked bench.",
    "GC1A011": "A short but steep traverse through an old quarry. Sturdy shoes recommended; the rock can be loose after rain.",
    "GC1A012": "A tiny nano hidden in a row of park benches. Great for a quick smiley on the way to somewhere else.",
}


def seed_demo_caches(work_dir: Path) -> None:
    """Import the 12 demo caches into the currently active (already-init'd) DB,
    then apply a few personal touches (found/flag/corrected coordinate) that
    aren't expressible via plain GPX import."""
    from opensak.db.database import get_session
    from opensak.importer import import_gpx

    gpx_file = write_gpx(work_dir, "demo.gpx", build_demo_gpx())
    with get_session() as session:
        import_gpx(gpx_file, session)

    # A handful of personal touches so the list/detail views look "lived in":
    # descriptions (cache_wpt() doesn't have a description param), one
    # corrected coordinate (final-not-at-posted-coords mystery), one user
    # flag, and "found" status on the two caches where AB_Green (the demo
    # user) logged a Found it above. GPX import only auto-detects found status
    # by matching a configured gc_username against log finders — there's no
    # such setting in this isolated test session, so we set it explicitly here
    # instead of relying on that path.
    from datetime import datetime, timezone

    from opensak.db.models import Cache, UserNote

    with get_session() as session:
        for cache in session.query(Cache).all():
            desc = DESCRIPTIONS.get(cache.gc_code)
            if desc:
                cache.short_description = desc

        # corrected_lat/lon live on UserNote (one-to-one with Cache), not on
        # Cache itself.
        mystery = session.query(Cache).filter_by(gc_code="GC1A005").one_or_none()
        if mystery is not None:
            session.add(UserNote(
                cache_id=mystery.id,
                corrected_lat=55.8420,
                corrected_lon=12.1105,
                is_corrected=True,
            ))

        flagged = session.query(Cache).filter_by(gc_code="GC1A011").one_or_none()
        if flagged is not None:
            flagged.user_flag = True


        for gc_code, found_date in (
            ("GC1A004", datetime(2026, 6, 1, 7, 45, tzinfo=timezone.utc)),
            ("GC1A010", datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc)),
        ):
            found_cache = session.query(Cache).filter_by(gc_code=gc_code).one_or_none()
            if found_cache is not None:
                found_cache.found = True
                found_cache.found_date = found_date
