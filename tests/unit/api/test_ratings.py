"""Choosing an age rating from TMDb certificates."""
from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from emby_dedupe.api import ratings as rt
from emby_dedupe.api.ratings import choose_rating, sk_step

# The household's kids'-time line: hidden when the rating holds an age of 15 or more, or is one
# of the age-less mature US ratings.
MATURE_RATINGS = {"R", "NC-17", "TV-MA", "X", "AO"}
KID_RATINGS = {"G", "TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "U", "0", "6", "7", "AL", "SK-U", "CZ-U"}


def hidden_in_kids_time(rating: str) -> bool:
    ages = [int(n) for n in re.findall(r"\d+", rating)]
    return rating in MATURE_RATINGS or bool(ages and max(ages) >= 15)


class TestSlovakScale:
    @pytest.mark.parametrize("age,expected", [(1, "SK-7"), (6, "SK-7"), (7, "SK-7"), (8, "SK-12"),
                                              (12, "SK-12"), (14, "SK-15"), (16, "SK-18"), (21, "SK-18")])
    def test_ages_round_up_to_the_next_slovak_step(self, age, expected):
        assert sk_step(age) == expected


class TestChooseRating:
    def test_slovak_certificate_wins_in_emby_form(self):
        assert choose_rating({"SK": ["12"], "DE": ["16"], "US": ["R"]}).rating == "SK-12"

    def test_us_certificate_is_written_bare_when_there_is_no_slovak_one(self):
        assert choose_rating({"US": ["PG-13"], "DE": ["16"]}).rating == "PG-13"

    def test_one_neighbour_gives_its_age_on_the_slovak_scale(self):
        choice = choose_rating({"CZ": ["15+"]})
        assert choice.rating == "SK-15"
        assert choice.reason == "neighbours 1"

    def test_two_neighbours_give_the_stricter(self):
        assert choose_rating({"CZ": ["12+"], "DE": ["16"]}).rating == "SK-18"

    def test_three_neighbours_give_the_middle(self):
        assert choose_rating({"CZ": ["12+"], "DE": ["16"], "GB": ["12A"]}).rating == "SK-12"

    def test_a_country_with_several_values_counts_its_highest(self):
        assert choose_rating({"DE": ["12", "16"]}).rating == "SK-18"

    def test_suitable_for_all_abroad_stays_empty(self):
        choice = choose_rating({"CZ": ["U"], "DE": ["0"]})
        assert choice.rating is None
        assert choice.reason == "suitable for all"

    def test_values_outside_the_official_lists_are_ignored(self):
        assert choose_rating({"CZ": ["NR", "Unrated"], "HU": ["X"]}).rating is None

    def test_other_countries_only_and_nothing_at_all_stay_empty(self):
        assert choose_rating({"FR": ["16"], "KR": ["15"]}).reason == "other countries only"
        assert choose_rating({}).reason == "no certificate"

    def test_series_use_the_tv_lists(self):
        assert choose_rating({"CZ": ["9+"]}, "tv").rating == "SK-12"
        assert choose_rating({"US": ["TV-MA"]}, "tv").rating == "TV-MA"


class TestAdvisorLine:
    """Every string the fill can produce from neighbours lands on the right side of the 15+ line."""

    def test_neighbour_outputs_match_the_age_they_stand_for(self):
        outputs = set()
        for tables in (rt.MOVIE_AGES, rt.TV_AGES):
            for country in rt.NEIGHBOURS:
                for value, age in tables[country].items():
                    choice = choose_rating({country: [value]}, "tv" if tables is rt.TV_AGES else "movie")
                    if choice.rating is None:
                        assert age == 0
                        continue
                    outputs.add(choice.rating)
                    assert hidden_in_kids_time(choice.rating) == (age >= 13), (country, value, choice.rating)
        assert outputs == {"SK-7", "SK-12", "SK-15", "SK-18"}
        assert not outputs & KID_RATINGS  # a proxy never marks a title as made for children


class TestCertificateSource:
    def _source(self, payload, status=200):
        source = rt.CertificateSource("token", cache={})
        response = MagicMock(status_code=status)
        response.json.return_value = payload
        response.raise_for_status = MagicMock()
        source._http = MagicMock()
        source._http.get.return_value = response
        source._limiter = MagicMock()
        return source

    def test_movie_release_dates_are_collected_per_country(self):
        source = self._source({"results": [
            {"iso_3166_1": "CZ", "release_dates": [{"certification": "12+"}, {"certification": ""}]},
            {"iso_3166_1": "DE", "release_dates": [{"certification": "16"}, {"certification": "16"}]},
            {"iso_3166_1": "FR", "release_dates": [{"certification": ""}]},
        ]})
        assert source.certificates("5", "movie") == {"CZ": ["12+"], "DE": ["16"]}

    def test_results_are_cached(self):
        source = self._source({"results": [{"iso_3166_1": "GB", "rating": "15"}]})
        assert source.certificates("7", "tv") == {"GB": ["15"]}
        assert source.certificates("7", "tv") == {"GB": ["15"]}
        assert source._http.get.call_count == 1

    def test_an_old_miss_is_looked_up_again(self):
        source = self._source({"results": []})
        source._cache["movie:9"] = {"at": 0, "certs": {}}
        assert source.certificates("9", "movie") == {}
        assert source._http.get.call_count == 1

    def test_unknown_item_is_none_and_not_cached(self):
        source = self._source({}, status=404)
        assert source.certificates("1", "movie") is None
        assert source._cache == {}
