"""Tariff parser: every line shape seen in the FBR PCT 2026-27 PDF's text layer."""

import pytest

from tariff.parse import ParsedTariff, TariffParseError, parse_lines


def parse(text: str) -> ParsedTariff:
    return parse_lines(text.strip().splitlines())


def paths(parsed: ParsedTariff) -> dict[str, str]:
    return {row.code: row.path for row in parsed.rows}


def test_groups_nest_by_dash_count_and_end_at_the_same_depth() -> None:
    parsed = parse("""
01.02 Live bovine animals.
- Cattle:
- - Pure-bred breeding animals:
0102.2110 - - - Bulls 0
0102.2120 - - - Cows 0
- - Other:
0102.2910 - - - Bulls 0
- Buffalo:
0102.3100 - - Pure-bred breeding animals 0
""")
    assert paths(parsed) == {
        "0102.2110": "Live bovine animals > Cattle > Pure-bred breeding animals > Bulls",
        "0102.2120": "Live bovine animals > Cattle > Pure-bred breeding animals > Cows",
        "0102.2910": "Live bovine animals > Cattle > Other > Bulls",
        "0102.3100": "Live bovine animals > Buffalo > Pure-bred breeding animals",
    }
    assert parsed.rows[0].description == "Bulls"


def test_wrapped_description_and_duty_on_its_own_line() -> None:
    parsed = parse("""
01.06 Other live animals.
- Mammals:
0106.1200 - - Whales, dolphins and porpoises (mammals of the order
Cetacea); manatees and dugongs
0
""")
    assert parsed.rows[0].description == (
        "Whales, dolphins and porpoises (mammals of the order Cetacea); manatees and dugongs"
    )
    assert parsed.without_trailing_duty == []


def test_a_number_that_ends_the_text_before_the_duty_is_kept() -> None:
    parsed = parse("""
20.09 Fruit juices.
2009.1200 - - Not frozen, of a Brix value not exceeding 20 20
""")
    assert parsed.rows[0].description == "Not frozen, of a Brix value not exceeding 20"


@pytest.mark.parametrize(
    "duty", ["Rs. 105 50/ MT", "Rs.1 5000 /MT", "Rs. 60 0/K g", "Rs.250/set", "Rs. 5 per meter"]
)
def test_specific_duties_are_stripped(duty: str) -> None:
    parsed = parse(f"""
15.07 Soya-bean oil.
1507.1000 - Crude oil, whether or not degummed {duty}
""")
    assert parsed.rows[0].description == "Crude oil, whether or not degummed"


def test_a_code_that_is_its_own_heading() -> None:
    parsed = parse("""
04.08 Birds' eggs.
0408.9900 - - Other 20
0409.0000 Natural honey. 20
0410.1000 - Insects 20
""")
    assert paths(parsed)["0409.0000"] == "Natural honey"
    assert parsed.rows[1].description == "Natural honey"


def test_code_references_in_wrapped_text_are_not_new_codes() -> None:
    parsed = parse("""
87.08 Parts and accessories of the motor vehicles of headings 87.01 to 87.05.
- Bumpers and parts thereof:
8708.1010 - - - (1) Bumpers for vehicles of sub - heading
8701.2020, 8701.2090, and 8701.2040
(2) Bumper for vehicles of sub - headings 8704.2219 and
8704.3130 and 8704.3190
8704.2390
25
8708.1090 - - - Other 30
""")
    assert [row.code for row in parsed.rows] == ["8708.1010", "8708.1090"]
    assert parsed.rows[0].description.endswith("8704.3130 and 8704.3190 8704.2390")


def test_a_bare_code_whose_text_starts_on_the_next_dashed_line() -> None:
    parsed = parse("""
84.79 Machines having individual functions.
- - Other:
8479.8950 - - - Tyre changers 0
8479.8960
- - - Flat/Conical base silos for grain storage 0
""")
    assert [row.code for row in parsed.rows] == ["8479.8950", "8479.8960"]
    assert parsed.rows[1].description == "Flat/Conical base silos for grain storage"
    assert paths(parsed)["8479.8960"] == (
        "Machines having individual functions > Other > Flat/Conical base silos for grain storage"
    )


def test_a_code_printed_without_dashes_is_a_sibling_of_the_code_above() -> None:
    parsed = parse("""
87.08 Parts and accessories.
- Bumpers and parts thereof:
8708.1010 - - - Bumpers (sheet metal) 25
8708.1020 (1) Following components for vehicles of heading 87.03 25
8708.1090 - - - Other 30
""")
    assert paths(parsed)["8708.1020"] == (
        "Parts and accessories > Bumpers and parts thereof > "
        "(1) Following components for vehicles of heading 87.03"
    )
    # The heading text is not replaced by the dashless code's text.
    assert paths(parsed)["8708.1090"] == "Parts and accessories > Bumpers and parts thereof > Other"


def test_deleted_headings_and_section_titles_end_the_entry_above() -> None:
    parsed = parse("""
05.02 Pigs' bristles.
0502.9000 - Other 20
[05.03]
27.16 Electrical energy.
2716.0000 Electrical energy. (optional heading) 5
I.CHEMICAL ELEMENTS
28.01 Fluorine, chlorine, bromine and iodine.
2801.1000 - Chlorine 0
III.- HALOGEN OR SULPHUR COMPOUNDS OF NON-
METALS
28.12 Halides of non-metals.
2812.1100 - - Carbonyl dichloride (phosgene) 0
""")
    assert [row.description for row in parsed.rows] == [
        "Other",
        "Electrical energy. (optional heading)",
        "Chlorine",
        "Carbonyl dichloride (phosgene)",
    ]


def test_a_continuation_that_looks_like_a_heading_number_is_text() -> None:
    parsed = parse("""
52.05 Cotton yarn.
5205.2700 - - Measuring less than 106.38 decitex but not less than
83.33 decitex 5
5205.2800 - - Measuring less than 83.33 decitex 5
""")
    assert parsed.rows[0].description == (
        "Measuring less than 106.38 decitex but not less than 83.33 decitex"
    )


def test_a_missing_heading_title_is_reported_not_guessed() -> None:
    parsed = parse("""
11.02 Cereal flours.
1102.9090 - - - Other 10
- Groats and meal:
1103.1100 - - Of wheat 15
""")
    assert parsed.untitled_headings == ["1103"]
    assert paths(parsed)["1103.1100"] == "Groats and meal > Of wheat"


def test_a_spreadsheet_error_description_is_reported_and_left_empty() -> None:
    parsed = parse("""
84.14 Air or vacuum pumps.
8414.1000 #NAME? 0
8414.2000 - Hand- or foot-operated air pumps 0
""")
    assert parsed.unreadable_descriptions == ["8414.1000"]
    assert parsed.rows[0].description == ""
    assert paths(parsed)["8414.1000"] == "Air or vacuum pumps"


def test_page_furniture_is_skipped_and_chapter_99_ends_the_table() -> None:
    parsed = parse("""
PAKISTAN CUSTOMS TARIFF FY 2026-27
97.06 Antiques of an age exceeding 100 years.
9706.1000 - Of an age exceeding 250 years 0
PCT CODE DESCRIPTION
CD (%)
9706.9000 - Other 0
9901
Goods imported by various agencies of the United Nations
0
""")
    assert [row.code for row in parsed.rows] == ["9706.1000", "9706.9000"]


def test_wrapped_hyphen_glyphs_become_hyphens_and_undecoded_glyphs_go() -> None:
    parsed = parse("""
48.02 Uncoated paper.
4802.5400 - - Weighing less than 40 g/ m\ufffd and photo\ufffesensitive 15
""")
    assert parsed.rows[0].description == "Weighing less than 40 g/ m and photo-sensitive"


def test_a_dashed_code_out_of_order_is_an_error() -> None:
    with pytest.raises(TariffParseError, match=r"0101\.2100: printed after 0101\.2900"):
        parse("""
01.01 Live horses.
0101.2900 - - Other 0
0101.2100 - - Pure-bred breeding animals 0
""")


def test_text_before_any_heading_is_an_error() -> None:
    with pytest.raises(TariffParseError, match="before the first heading"):
        parse("some stray text\n01.01 Live horses.\n0101.2100 - - Pure-bred 0")


def test_no_rows_is_an_error() -> None:
    with pytest.raises(TariffParseError, match="no tariff rows"):
        parse("01.01 Live horses.")
