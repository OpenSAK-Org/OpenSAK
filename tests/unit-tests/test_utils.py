"""tests/unit-tests/test_utils.py — validate_gc_code() and get_import_type()."""

import pytest
from pathlib import Path
from opensak.utils.utils import validate_gc_code, get_import_type
from opensak.utils.types import ImportType


# ── validate_gc_code ──────────────────────────────────────────────────────────

class TestValidateGcCodeValid:
    def test_minimum_length(self):
        validate_gc_code("GCA")  # 1 char suffix — should not raise

    def test_maximum_length(self):
        validate_gc_code("GC1234567")  # 7 char suffix — should not raise

    def test_all_digits(self):
        validate_gc_code("GC12345")

    def test_mixed_alphanumeric(self):
        validate_gc_code("GCAB123")

    def test_lowercase_input_is_accepted(self):
        # The function uppercases before matching
        validate_gc_code("gcabc12")

    def test_letters_a_through_n(self):
        validate_gc_code("GCABCDE")
        validate_gc_code("GCGHIJK")
        validate_gc_code("GCLMN12")

    def test_letters_p_through_r(self):
        validate_gc_code("GCPQR12")

    def test_letters_t_through_z(self):
        validate_gc_code("GCTUVWX")

    def test_real_world_code(self):
        validate_gc_code("GC1234")

    def test_four_char_suffix(self):
        validate_gc_code("GCABC1")


class TestValidateGcCodeInvalid:
    def test_missing_gc_prefix(self):
        with pytest.raises(ValueError):
            validate_gc_code("AB1234")

    def test_wrong_prefix(self):
        with pytest.raises(ValueError):
            validate_gc_code("WP1234")

    def test_empty_suffix(self):
        with pytest.raises(ValueError):
            validate_gc_code("GC")

    def test_too_long_suffix(self):
        with pytest.raises(ValueError):
            validate_gc_code("GC12345678")  # 8 chars — over limit

    def test_excluded_letter_o(self):
        with pytest.raises(ValueError):
            validate_gc_code("GCOABCD")

    def test_excluded_letter_s(self):
        with pytest.raises(ValueError):
            validate_gc_code("GCSABCD")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            validate_gc_code("")

    def test_special_characters(self):
        with pytest.raises(ValueError):
            validate_gc_code("GC!@#$%")

    def test_spaces_in_code(self):
        with pytest.raises(ValueError):
            validate_gc_code("GC 1234")

    def test_only_prefix(self):
        with pytest.raises(ValueError):
            validate_gc_code("GC")

    def test_raises_value_error_type(self):
        with pytest.raises(ValueError, match="Invalid gc_code format"):
            validate_gc_code("INVALID")


# ── get_import_type ───────────────────────────────────────────────────────────

class TestGetImportType:
    def test_gpx_extension(self):
        assert get_import_type(Path("mycaches.gpx")) == ImportType.GPX

    def test_zip_extension(self):
        assert get_import_type(Path("pocket_query.zip")) == ImportType.ZIP

    def test_gpx_uppercase(self):
        assert get_import_type(Path("caches.GPX")) == ImportType.GPX

    def test_zip_uppercase(self):
        assert get_import_type(Path("archive.ZIP")) == ImportType.ZIP

    def test_gpx_mixed_case(self):
        assert get_import_type(Path("export.Gpx")) == ImportType.GPX

    def test_full_path_gpx(self):
        assert get_import_type(Path("/home/user/downloads/query.gpx")) == ImportType.GPX

    def test_full_path_zip(self):
        assert get_import_type(Path("/tmp/exports/batch.zip")) == ImportType.ZIP

    def test_unsupported_loc_raises(self):
        with pytest.raises(ValueError, match="Unsupported file format"):
            get_import_type(Path("caches.loc"))

    def test_unsupported_txt_raises(self):
        with pytest.raises(ValueError, match="Unsupported file format"):
            get_import_type(Path("notes.txt"))

    def test_no_extension_raises(self):
        with pytest.raises(ValueError, match="Unsupported file format"):
            get_import_type(Path("noextension"))

    def test_unsupported_json_raises(self):
        with pytest.raises(ValueError, match="Unsupported file format"):
            get_import_type(Path("data.json"))
