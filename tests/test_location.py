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
        # An unrecognised trailing token is not swallowed as a country.
        ("Springfield, Narnia", ("springfield narnia", "", "")),
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
