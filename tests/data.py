"""tests/data.py — synthetic GPX/.loc test data and programmatic builders."""

import zipfile
from pathlib import Path


# ── Hand-written GPX matching real Groundspeak/PQ format ─────────────────────

SAMPLE_GPX = """\
<?xml version="1.0" encoding="utf-8"?>
<gpx xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
     version="1.0"
     creator="Groundspeak Pocket Query"
     xmlns="http://www.topografix.com/GPX/1/0">
  <n>Test PQ</n>
  <time>2026-03-10T07:22:19Z</time>
  <wpt lat="55.6761" lon="12.5683">
    <time>2024-06-01T00:00:00</time>
    <n>GC12345</n>
    <desc>Test Traditional by TestOwner, Traditional Cache (2.0/3.0)</desc>
    <url>https://coord.info/GC12345</url>
    <urlname>Test Traditional</urlname>
    <sym>Geocache</sym>
    <type>Geocache|Traditional Cache</type>
    <groundspeak:cache id="9999" archived="False" available="True"
        xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1">
      <groundspeak:name>Test Traditional</groundspeak:name>
      <groundspeak:placed_by>TestOwner</groundspeak:placed_by>
      <groundspeak:owner id="12345">TestOwner</groundspeak:owner>
      <groundspeak:type>Traditional Cache</groundspeak:type>
      <groundspeak:container>Small</groundspeak:container>
      <groundspeak:attributes>
        <groundspeak:attribute id="6" inc="1">Recommended for kids</groundspeak:attribute>
        <groundspeak:attribute id="24" inc="0">Wheelchair accessible</groundspeak:attribute>
      </groundspeak:attributes>
      <groundspeak:difficulty>2.0</groundspeak:difficulty>
      <groundspeak:terrain>3.0</groundspeak:terrain>
      <groundspeak:country>Denmark</groundspeak:country>
      <groundspeak:state>Zealand</groundspeak:state>
      <groundspeak:county>Copenhagen</groundspeak:county>
      <groundspeak:short_description html="False">A short description.</groundspeak:short_description>
      <groundspeak:long_description html="True">&lt;p&gt;A longer description.&lt;/p&gt;</groundspeak:long_description>
      <groundspeak:encoded_hints>Under a rock.</groundspeak:encoded_hints>
      <groundspeak:logs>
        <groundspeak:log id="111">
          <groundspeak:date>2026-01-15T10:30:00Z</groundspeak:date>
          <groundspeak:type>Found it</groundspeak:type>
          <groundspeak:finder id="999">Tester</groundspeak:finder>
          <groundspeak:text encoded="False">TFTC! Great hide.</groundspeak:text>
        </groundspeak:log>
        <groundspeak:log id="112">
          <groundspeak:date>2025-12-01T09:00:00Z</groundspeak:date>
          <groundspeak:type>Didn't find it</groundspeak:type>
          <groundspeak:finder id="888">Searcher</groundspeak:finder>
          <groundspeak:text encoded="False">Could not find it.</groundspeak:text>
        </groundspeak:log>
      </groundspeak:logs>
    </groundspeak:cache>
  </wpt>
  <wpt lat="56.1234" lon="10.5678">
    <time>2023-03-15T00:00:00</time>
    <n>GC99999</n>
    <desc>Mystery Cache by Puzzler, Unknown Cache (4.0/2.5)</desc>
    <url>https://coord.info/GC99999</url>
    <urlname>Mystery Cache</urlname>
    <sym>Geocache</sym>
    <type>Geocache|Unknown Cache</type>
    <groundspeak:cache id="8888" archived="False" available="True"
        xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1">
      <groundspeak:name>Mystery Cache</groundspeak:name>
      <groundspeak:placed_by>Puzzler</groundspeak:placed_by>
      <groundspeak:owner id="54321">Puzzler</groundspeak:owner>
      <groundspeak:type>Unknown Cache</groundspeak:type>
      <groundspeak:container>Micro</groundspeak:container>
      <groundspeak:attributes/>
      <groundspeak:difficulty>4.0</groundspeak:difficulty>
      <groundspeak:terrain>2.5</groundspeak:terrain>
      <groundspeak:country>Denmark</groundspeak:country>
      <groundspeak:state>Region Midtjylland</groundspeak:state>
      <groundspeak:county>Aarhus</groundspeak:county>
      <groundspeak:short_description html="False"></groundspeak:short_description>
      <groundspeak:long_description html="False">Solve the puzzle first.</groundspeak:long_description>
      <groundspeak:encoded_hints>Check the tree.</groundspeak:encoded_hints>
      <groundspeak:logs/>
    </groundspeak:cache>
  </wpt>
</gpx>
"""

SAMPLE_WPTS_GPX = """\
<?xml version="1.0" encoding="utf-8"?>
<gpx xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
     version="1.0"
     creator="Groundspeak, Inc."
     xmlns="http://www.topografix.com/GPX/1/0">
  <n>Waypoints for Cache Listings</n>
  <wpt lat="55.6762" lon="12.5680">
    <n>PK2345</n>
    <desc>Parking Area</desc>
    <sym>Parking Area</sym>
    <type>Waypoint|Parking Area</type>
    <cmt>Park here and walk 200m south.</cmt>
  </wpt>
</gpx>
"""

EMPTY_GPX = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<gpx xmlns="http://www.topografix.com/GPX/1/0" version="1.0">'
    '<n>Empty</n></gpx>'
)

INLINE_WPT_SUFFIX = (
    '  <wpt lat="55.6770" lon="12.5690">\n'
    '    <n>PK12345</n>\n'
    '    <desc>Parking Area</desc>\n'
    '    <sym>Parking Area</sym>\n'
    '    <type>Waypoint|Parking Area</type>\n'
    '    <cmt>Street parking available.</cmt>\n'
    '  </wpt>\n'
    '</gpx>'
)


def make_variant_gpx(gc1="GCABCDE", gc2="GCFGHIJ", log1="333", log2="444"):
    """Return a SAMPLE_GPX variant with different GC codes and log IDs."""
    return (SAMPLE_GPX
        .replace("GC12345", gc1)
        .replace("GC99999", gc2)
        .replace('id="111"', f'id="{log1}"')
        .replace('id="112"', f'id="{log2}"'))


def make_gpx_with_inline_wpt():
    """Return SAMPLE_GPX with an extra inline parking waypoint appended."""
    return SAMPLE_GPX.replace("</gpx>", INLINE_WPT_SUFFIX)


def write_gpx(tmp_path: Path, name: str, content: str) -> Path:
    """Write GPX content to a file and return the path."""
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return f


def make_zip(tmp_path: Path, name: str, files: dict[str, str | Path]) -> Path:
    """Create a zip from a dict of {archive_name: content_or_path}.

    Values can be either a string (written to a temp file first) or a Path.
    """
    z = tmp_path / name
    with zipfile.ZipFile(z, "w") as zf:
        for archive_name, content in files.items():
            if isinstance(content, Path):
                zf.write(content, archive_name)
            else:
                p = tmp_path / f"_tmp_{archive_name}"
                p.write_text(content, encoding="utf-8")
                zf.write(p, archive_name)
    return z


# ── Programmatic builders (for tests needing specific ids/logs/coords) ───────

def cache_wpt(
    gc_code: str,
    *,
    name: str | None = None,
    lat: float | str = 55.0,
    lon: float | str = 12.0,
    cache_type: str = "Traditional Cache",
    gs_id: int | str = 1,
    placed_by: str = "Tester",
    container: str = "Small",
    difficulty: float = 1.5,
    terrain: float = 2.0,
    archived: bool = False,
    available: bool = True,
    hint: str | None = None,
    logs: list[dict] | None = None,
    attributes: list[dict] | None = None,
) -> str:
    """One Groundspeak ``<wpt>`` block. logs keys: id/type/finder/finder_id/date/text;
    attributes keys: id/inc/name. Wrap with build_gpx() for a full document."""
    name = name or gc_code
    log_xml = "".join(
        f'<groundspeak:log id="{lg.get("id", i + 1)}">'
        f'<groundspeak:date>{lg.get("date", "2025-01-01T00:00:00Z")}</groundspeak:date>'
        f'<groundspeak:type>{lg.get("type", "Found it")}</groundspeak:type>'
        f'<groundspeak:finder id="{lg.get("finder_id", i + 1)}">{lg.get("finder", "Finder")}</groundspeak:finder>'
        f'<groundspeak:text encoded="False">{lg.get("text", "log")}</groundspeak:text>'
        "</groundspeak:log>"
        for i, lg in enumerate(logs or [])
    )
    attr_xml = "".join(
        f'<groundspeak:attribute id="{at.get("id", 1)}" inc="{at.get("inc", 1)}">'
        f'{at.get("name", "")}</groundspeak:attribute>'
        for at in (attributes or [])
    )
    hint_xml = (
        f"<groundspeak:encoded_hints>{hint}</groundspeak:encoded_hints>" if hint else ""
    )
    return (
        f'<wpt lat="{lat}" lon="{lon}">'
        f"<n>{gc_code}</n>"
        f"<urlname>{name}</urlname>"
        f"<type>Geocache|{cache_type}</type>"
        f'<groundspeak:cache id="{gs_id}" archived="{archived}" available="{available}" '
        'xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1">'
        f"<groundspeak:name>{name}</groundspeak:name>"
        f"<groundspeak:placed_by>{placed_by}</groundspeak:placed_by>"
        f"<groundspeak:type>{cache_type}</groundspeak:type>"
        f"<groundspeak:container>{container}</groundspeak:container>"
        f"<groundspeak:difficulty>{difficulty}</groundspeak:difficulty>"
        f"<groundspeak:terrain>{terrain}</groundspeak:terrain>"
        f"{hint_xml}"
        f"<groundspeak:attributes>{attr_xml}</groundspeak:attributes>"
        f"<groundspeak:logs>{log_xml}</groundspeak:logs>"
        "</groundspeak:cache></wpt>"
    )


def build_gpx(*wpt_blocks: str, creator: str = "Groundspeak Pocket Query") -> str:
    """Wrap one or more ``<wpt>`` blocks (from :func:`cache_wpt`) into a GPX doc."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<gpx version="1.0" creator="{creator}" '
        'xmlns="http://www.topografix.com/GPX/1/0">'
        f'{"".join(wpt_blocks)}</gpx>'
    )


def make_loc(waypoints: list[dict]) -> str:
    """Build a ``.loc`` document from waypoint dicts (keys: gc_code, name?, lat, lon)."""
    wpts = "".join(
        "\n  <waypoint>"
        f'\n    <name id="{wp["gc_code"]}">{wp.get("name", wp["gc_code"])}</name>'
        f'\n    <coord lat="{wp["lat"]}" lon="{wp["lon"]}"/>'
        "\n  </waypoint>"
        for wp in waypoints
    )
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<loc version="1.0">{wpts}\n</loc>'


def seed_standard_caches(work_dir: Path) -> None:
    """Import the 4 standard sample caches (GC12345 +PK waypoint, GC99999, GCAAA01,
    GCAAA02) into the active DB. init_db() must already point at the target DB."""
    from opensak.db.database import get_session
    from opensak.importer import import_gpx

    gpx_file = write_gpx(work_dir, "sample.gpx", SAMPLE_GPX)
    wpts_file = write_gpx(work_dir, "sample-wpts.gpx", SAMPLE_WPTS_GPX)
    with get_session() as session:
        import_gpx(gpx_file, session, wpts_path=wpts_file)

    variant_file = write_gpx(work_dir, "variant.gpx", make_variant_gpx("GCAAA01", "GCAAA02"))
    with get_session() as session:
        import_gpx(variant_file, session)


def make_fake_manager(db_path: Path, name: str = "E2ETest"):
    """Return a lightweight DatabaseManager stand-in for monkeypatching."""
    from opensak.db.manager import DatabaseInfo

    class _FakeManager:
        def __init__(self):
            self._info = DatabaseInfo(name, db_path)

        @property
        def active(self):
            return self._info

        @property
        def active_path(self):
            return self._info.path

        @property
        def databases(self):
            return [self._info]

        def ensure_active_initialised(self):
            pass

        def switch_to(self, db_info):
            pass

        def new_database(self, _name, path=None):
            raise RuntimeError("new_database called during e2e test")

    return _FakeManager()
