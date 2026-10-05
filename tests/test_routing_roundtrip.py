"""Tests for the explicit converter round-trip checker.

Targeted run command:

    python -m pytest tests/test_routing_roundtrip.py -q
"""

from __future__ import annotations

import pytest

from werkzeug.routing import check_roundtrip
from werkzeug.routing import IntegerConverter
from werkzeug.routing import Map
from werkzeug.routing import Rule
from werkzeug.routing import ValidationError
from werkzeug.routing.roundtrip import BAD_CONVERTER_EXAMPLE
from werkzeug.routing.roundtrip import FINITE_GUARANTEE
from werkzeug.routing.roundtrip import PYTEST_COMMAND


def counting_compare():
    """A compare function that records how often it was called."""
    calls = []

    def compare(decoded, original):
        calls.append((decoded, original))
        return decoded == original

    return compare, calls


class BadSegmentConverter(IntegerConverter):
    """Deliberately bad converter: ``to_url`` produces another segment."""

    def to_url(self, value):
        return f"{int(value)}/extra"


class BadDecodeConverter(IntegerConverter):
    """Deliberately bad converter: ``to_python`` rejects its own output."""

    def to_python(self, value):
        raise ValidationError("decode always fails")


class BadEncodeConverter(IntegerConverter):
    """Deliberately bad converter: ``to_url`` raises."""

    def to_url(self, value):
        raise ValidationError("encode always fails")


def test_int_converter_twelve_samples_pass():
    rule = Rule("/item/<int:n>", endpoint="item")
    Map([rule])
    compare, calls = counting_compare()

    report = check_roundtrip(rule, list(range(12)), compare=compare)

    assert report.ok, report.summary()
    assert report.total == 12
    assert len(report.passed) == 12
    assert report.failures == []
    # The custom compare ran exactly once per successful sample.
    assert len(calls) == 12
    assert report.compare_calls == 12
    # No view function was dispatched.
    assert report.view_calls == 0


def test_int_converter_dict_samples_pass():
    rule = Rule("/item/<int(signed=True):n>", endpoint="item")
    Map([rule])

    report = check_roundtrip(rule, [{"n": -3}, {"n": 0}, {"n": 42}])

    assert report.ok, report.summary()
    assert len(report.passed) == 3


def test_bad_converter_crossing_segment_fails():
    rule = Rule("/item/<bad:n>", endpoint="item")
    Map([rule], converters={"bad": BadSegmentConverter})
    compare, calls = counting_compare()

    report = check_roundtrip(rule, list(range(12)), compare=compare)

    assert not report.ok
    assert len(report.failures) == 12
    assert {f.direction for f in report.failures} == {"segment"}
    assert all("crosses a path segment" in f.message for f in report.failures)
    # Compare is never reached for failed samples.
    assert calls == []
    assert report.compare_calls == 0


def test_decode_failure_reported_by_direction():
    rule = Rule("/item/<bad:n>", endpoint="item")
    Map([rule], converters={"bad": BadDecodeConverter})

    report = check_roundtrip(rule, [1, 2, 3])

    assert not report.ok
    assert {f.direction for f in report.failures} == {"to_python"}
    assert all(isinstance(f.exception, ValidationError) for f in report.failures)


def test_encode_failure_reported_by_direction():
    rule = Rule("/item/<bad:n>", endpoint="item")
    Map([rule], converters={"bad": BadEncodeConverter})

    report = check_roundtrip(rule, [1, 2, 3])

    assert not report.ok
    assert {f.direction for f in report.failures} == {"to_url"}
    assert all(isinstance(f.exception, ValidationError) for f in report.failures)


def test_compare_called_at_most_once_per_sample():
    rule = Rule("/item/<int:n>", endpoint="item")
    Map([rule])
    compare, calls = counting_compare()

    report = check_roundtrip(rule, list(range(12)), compare=compare)

    assert report.ok
    assert len(calls) == 12
    assert report.compare_calls == 12
    # Each call compared the decoded value with the original sample.
    assert sorted(original for _, original in calls) == list(range(12))


def test_compare_rejection_reported():
    rule = Rule("/item/<int:n>", endpoint="item")
    Map([rule])

    report = check_roundtrip(rule, [1, 2], compare=lambda decoded, original: False)

    assert not report.ok
    assert {f.direction for f in report.failures} == {"compare"}
    # The compare function was still called only once per sample.
    assert report.compare_calls == 2


def test_view_functions_are_not_called():
    view_calls = []

    def item_view(n):
        view_calls.append(n)
        return "ok"

    views = {"item": item_view}
    rule = Rule("/item/<int:n>", endpoint="item")
    Map([rule])

    report = check_roundtrip(rule, list(range(12)))

    assert report.ok, report.summary()
    # The checker only routes and converts; it never dispatches.
    assert view_calls == []
    assert views["item"] is item_view
    assert report.view_calls == 0


def test_report_states_finite_guarantee():
    rule = Rule("/item/<int:n>", endpoint="item")
    Map([rule])

    report = check_roundtrip(rule, list(range(12)))
    summary = report.summary()

    assert report.ok
    # The finite guarantee is written into the report.
    assert FINITE_GUARANTEE.format(total=12) in summary
    assert "does NOT claim" in summary
    # The report points at the targeted pytest command.
    assert PYTEST_COMMAND in summary
    assert "view function calls: 0" in summary


def test_bad_converter_example_is_documented():
    assert "BadIntConverter" in BAD_CONVERTER_EXAMPLE
    assert "/extra" in BAD_CONVERTER_EXAMPLE
    assert PYTEST_COMMAND == "python -m pytest tests/test_routing_roundtrip.py -q"


def test_unbound_rule_rejected():
    rule = Rule("/item/<int:n>", endpoint="item")

    with pytest.raises(ValueError, match="bound"):
        check_roundtrip(rule, [1])


def test_missing_sample_argument_rejected():
    rule = Rule("/item/<int:n>", endpoint="item")
    Map([rule])

    with pytest.raises(ValueError, match="missing"):
        check_roundtrip(rule, [{"other": 1}])


class LaxConverter(IntegerConverter):
    """Encodes values its own regex would not match."""

    regex = r"[0-9]{2}"

    def to_url(self, value):
        return str(int(value))

    def to_python(self, value):
        return int(value)


def test_encoded_result_must_still_match_rule():
    rule = Rule("/item/<lax:n>", endpoint="item")
    Map([rule], converters={"lax": LaxConverter})

    report = check_roundtrip(rule, [5, 42])

    assert not report.ok
    # "5" encodes to "5", which the rule's two-digit regex rejects.
    assert [(f.sample, f.direction) for f in report.failures] == [({"n": 5}, "match")]
    # "42" still round-trips.
    assert report.passed == [{"n": 42}]
