import pytest

from jobpilot.store.location import parse_location


@pytest.mark.parametrize(
    "location,expected",
    [
        # Nothing given at all.
        (None, ("", "", "")),
        ("", ("", "", "")),
        ("   ", ("", "", "")),
        # Naukri spells everything out.
        ("Gurgaon, Haryana, India", ("gurgaon", "haryana", "india")),
        ("Mumbai, Maharashtra, India", ("mumbai", "maharashtra", "india")),
        # Indeed India abbreviates down to state + country, no city at all.
        # This is the real shape captured live on 2026-09-19 and the exact
        # bug this module exists to fix: "hr" must not become a city.
        ("HR, IN", ("", "haryana", "india")),
        ("UP, IN", ("", "uttar pradesh", "india")),
        # Bare country is a real signal (no city), not a city named "india".
        ("India", ("", "", "india")),
        ("United States", ("", "", "united states")),
        ("USA", ("", "", "united states")),
        # Indeed US: city, state, country all spelled/abbreviated together.
        ("Austin, TX, US", ("austin", "texas", "united states")),
        ("O'Fallon, MO, US", ("o fallon", "missouri", "united states")),
        # A remote listing carries no city — "Remote, US" is not a city
        # named "remote" (final-review finding, Ruling 21 evidence).
        ("Remote, US", ("", "", "united states")),
        ("Remote", ("", "", "")),
        # City-alias table still applies, exactly as before.
        ("Gurugram, Haryana, India", ("gurgaon", "haryana", "india")),
        ("Delhi NCR, India", ("delhi", "", "india")),
        ("Delhi / NCR", ("delhi", "", "")),
        ("NCR", ("delhi", "", "")),
        ("Bombay, Maharashtra, India", ("mumbai", "maharashtra", "india")),
        # A bare city with nothing else is still a city.
        ("Gurgaon", ("gurgaon", "", "")),
        # An unrecognised trailing token is not swallowed as a country; the
        # LAST remaining part is the city, not every part joined (see the
        # street-address test below for why).
        ("Springfield, Narnia", ("narnia", "", "")),
    ],
)
def test_parse_location(location, expected):
    assert parse_location(location) == expected


def test_us_and_indian_state_codes_do_not_collide():
    """'LA' means Ladakh in an Indian address and Louisiana in a US one.

    The country decides which table to consult, so the same two-letter code
    resolves differently depending on where it was seen.
    """
    assert parse_location("LA, IN") == ("", "ladakh", "india")
    assert parse_location("Lafayette, LA, US") == ("lafayette", "louisiana", "united states")


@pytest.mark.parametrize(
    "location,expected",
    [
        # A UT/state name that IS also the bare city people mean must stay a
        # city, exactly like Delhi -- a region-table review found this
        # regression: consuming the full word as a region left these with
        # NO city at all, which made the same real job in, say, Chandigarh
        # collide with an unrelated city-less role for the same employer.
        ("Chandigarh, India", ("chandigarh", "", "india")),
        ("Goa, India", ("goa", "", "india")),
        ("Puducherry, India", ("puducherry", "", "india")),
        # Same shape on the US side: the state name IS the famous city name.
        ("New York, US", ("new york", "", "united states")),
        ("Washington, US", ("washington", "", "united states")),
        # The short codes for the same places stay real regions -- nobody
        # writes a bare city called "CH", "GA", "PY", "NY" or "WA".
        ("CH, IN", ("", "chandigarh", "india")),
        ("GA, IN", ("", "goa", "india")),
        ("PY, IN", ("", "puducherry", "india")),
        ("NY, US", ("", "new york", "united states")),
        ("WA, US", ("", "washington", "united states")),
    ],
)
def test_a_ut_or_state_that_is_also_the_bare_city_name_stays_a_city(location, expected):
    assert parse_location(location) == expected


def test_a_street_or_locality_prefix_does_not_change_the_city():
    """Independent-review finding: a real job listed with and without a
    locality/street prefix must resolve to the SAME city, not two rows for
    the same real role."""
    with_prefix = parse_location("Udyog Vihar, Gurgaon, Haryana, India")
    without_prefix = parse_location("Gurgaon, Haryana, India")
    assert with_prefix == without_prefix == ("gurgaon", "haryana", "india")


def test_a_domestic_us_pair_with_no_country_suffix_is_an_accepted_gap():
    """ACCEPTED LIMITATION, not fixed: a bare "City, IN" with no explicit
    country reads "IN" as India (the trailing position is checked as a
    country first), when a 2-part US-domestic listing giving only city and
    state would mean Indiana. Every real Indeed US row captured so far
    (2026-09-19) carries an explicit trailing "US"/"IN" is never left bare,
    so this is a real but so-far-unobserved gap, not a live bug."""
    assert parse_location("Indianapolis, IN") == ("indianapolis", "", "india")
    # The moment a country IS given, the real ambiguity disappears.
    assert parse_location("Indianapolis, IN, US") == ("indianapolis", "indiana", "united states")


def test_a_foreign_countrys_own_code_can_cross_match_our_tables_accepted_risk():
    """ACCEPTED, BOUNDED RISK, not fixed: 'UK' (United Kingdom) is not a
    country this project models, but it IS the Indian abbreviation for
    Uttarakhand, so a genuinely foreign "London, UK" reads as a state-level
    Uttarakhand sighting. Trying both tables when no KNOWN country matched
    is required for real, country-less Naukri/Indeed rows ("Gurugram,
    Haryana" -- see test_title_and_location_are_last_writer_wins... in
    test_store.py), and nothing in this project ever scrapes a source
    outside India/US, so a real foreign string never actually reaches this
    code today. Revisit if that ever changes."""
    assert parse_location("London, UK") == ("london", "uttarakhand", "")
    # The identical two letters still work correctly when they genuinely
    # DO follow a country JobSpy actually reports for these markets.
    assert parse_location("UK, IN") == ("", "uttarakhand", "india")
