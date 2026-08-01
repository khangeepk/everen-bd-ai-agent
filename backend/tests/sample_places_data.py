"""Sample Places API responses keyed by ZIP code, for tests.

Shaped like real Places API (New) ``places:searchText`` responses but entirely
fabricated -- no real place IDs, businesses, or addresses. Fake data keeps the
suite offline and avoids storing genuine Google Maps Content in the repo.

Covers three ZIPs:

* ``78701`` -- Austin, TX. Four results, one missing coordinates.
* ``10001`` -- New York, NY. Three results, one overlapping with 78701 to
  exercise cross-search dedup.
* ``00000`` -- Empty result set.
"""

from __future__ import annotations

from typing import Any

#: A place appearing in both ZIP result sets, to test dedup across searches.
SHARED_PLACE_ID = "ChIJ_TEST_SHARED_0000000001"

AUSTIN_78701: dict[str, Any] = {
    "places": [
        {
            "id": "ChIJ_TEST_AUSTIN_00000001",
            "displayName": {"text": "Congress Avenue Dental", "languageCode": "en"},
            "formattedAddress": "100 Congress Ave, Austin, TX 78701, USA",
            "location": {"latitude": 30.2669, "longitude": -97.7428},
            "websiteUri": "https://example-congress-dental.test",
            "nationalPhoneNumber": "(512) 555-0101",
            "businessStatus": "OPERATIONAL",
        },
        {
            "id": "ChIJ_TEST_AUSTIN_00000002",
            "displayName": {"text": "Lady Bird Family Dentistry", "languageCode": "en"},
            "formattedAddress": "200 W 2nd St, Austin, TX 78701, USA",
            "location": {"latitude": 30.2649, "longitude": -97.7478},
            "websiteUri": "https://example-ladybird-dental.test",
            "nationalPhoneNumber": "(512) 555-0102",
            "businessStatus": "OPERATIONAL",
        },
        {
            # No location block: exercises the coordinates-optional path.
            "id": "ChIJ_TEST_AUSTIN_00000003",
            "displayName": {"text": "Sixth Street Orthodontics", "languageCode": "en"},
            "formattedAddress": "300 E 6th St, Austin, TX 78701, USA",
            "businessStatus": "OPERATIONAL",
        },
        {
            "id": SHARED_PLACE_ID,
            "displayName": {"text": "Nationwide Dental Group", "languageCode": "en"},
            "formattedAddress": "400 Colorado St, Austin, TX 78701, USA",
            "location": {"latitude": 30.2685, "longitude": -97.7441},
            "businessStatus": "OPERATIONAL",
        },
    ]
}

NEW_YORK_10001: dict[str, Any] = {
    "places": [
        {
            "id": "ChIJ_TEST_NYC_000000001",
            "displayName": {"text": "Chelsea Smile Studio", "languageCode": "en"},
            "formattedAddress": "500 W 30th St, New York, NY 10001, USA",
            "location": {"latitude": 40.7506, "longitude": -73.9971},
            "nationalPhoneNumber": "(212) 555-0201",
            "businessStatus": "OPERATIONAL",
        },
        {
            "id": "ChIJ_TEST_NYC_000000002",
            "displayName": {"text": "Herald Square Dental Care", "languageCode": "en"},
            "formattedAddress": "600 6th Ave, New York, NY 10001, USA",
            "location": {"latitude": 40.7484, "longitude": -73.9878},
            "businessStatus": "CLOSED_TEMPORARILY",
        },
        {
            # Same place_id as an Austin result: dedup must recognize it.
            "id": SHARED_PLACE_ID,
            "displayName": {"text": "Nationwide Dental Group", "languageCode": "en"},
            "formattedAddress": "700 8th Ave, New York, NY 10001, USA",
            "location": {"latitude": 40.7549, "longitude": -73.9900},
            "businessStatus": "OPERATIONAL",
        },
    ]
}

EMPTY_00000: dict[str, Any] = {"places": []}

#: Malformed entries the parser must skip without raising.
MALFORMED: dict[str, Any] = {
    "places": [
        {"displayName": {"text": "No ID Business"}},
        {"id": "", "displayName": {"text": "Blank ID Business"}},
        {"id": "   ", "displayName": {"text": "Whitespace ID Business"}},
        {
            "id": "ChIJ_TEST_VALID_000000001",
            "displayName": {"text": "Valid Business"},
            "location": {"latitude": 1.0, "longitude": 2.0},
        },
    ]
}

RESPONSES_BY_ZIP: dict[str, dict[str, Any]] = {
    "78701": AUSTIN_78701,
    "10001": NEW_YORK_10001,
    "00000": EMPTY_00000,
}

#: Every business name and address in the fixtures. A compliance test asserts
#: none of these strings ever appears in a persisted column.
FORBIDDEN_STRINGS: tuple[str, ...] = (
    "Congress Avenue Dental",
    "Lady Bird Family Dentistry",
    "Sixth Street Orthodontics",
    "Nationwide Dental Group",
    "Chelsea Smile Studio",
    "Herald Square Dental Care",
    "100 Congress Ave",
    "200 W 2nd St",
    "300 E 6th St",
    "400 Colorado St",
    "500 W 30th St",
    "600 6th Ave",
    "700 8th Ave",
    "(512) 555-0101",
    "(512) 555-0102",
    "(212) 555-0201",
    "example-congress-dental.test",
    "example-ladybird-dental.test",
)
