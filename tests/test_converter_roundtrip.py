"""Explicit round-trip checker for rule converters.

This module provides :func:`check_converter_roundtrip`, a finite, sample-based
check that a converter's ``to_url`` then ``to_python`` path preserves identity.

.. warning::

    This is a **finite sample check**. It only verifies that the given samples
    round-trip. It does **not** prove that the converter is correct for all
    possible values. Do not claim global equivalence from the report. View
    functions are never called.

Pytest targeted command::

    pytest tests/test_converter_roundtrip.py -v
"""

from __future__ import annotations

import re
import typing as t
from dataclasses import dataclass

from werkzeug.routing import BaseConverter
from werkzeug.routing import Map
from werkzeug.routing import Rule
from werkzeug.routing import ValidationError
from werkzeug.routing.exceptions import RequestRedirect

# ---------------------------------------------------------------------------
# Round-trip checker
# ---------------------------------------------------------------------------


@dataclass
class RoundTripFailure:
    """A failed round trip, tagged by the direction that failed.

    ``direction`` is one of:

    - ``"to_url"``: the converter's ``to_url`` raised or returned an invalid
      value in the build direction.
    - ``"segment"``: the encoded value did not match the converter's regex,
      crossed a path segment (for an isolating converter), or the built URL
      did not match the original rule.
    - ``"to_python"``: the converter's ``to_python`` raised in the parse
      direction.
    - ``"identity"``: the parsed value did not compare equal to the original.
    """

    direction: str
    value: t.Any
    message: str


@dataclass
class RoundTripReport:
    """The result of checking a finite set of samples.

    .. warning::

        This is a **finite, sample-based** result. It only proves that the
        given samples round-trip through ``to_url`` then ``to_python``. It
        does **not** prove that the converter is correct for all possible
        values. Do not claim global equivalence from this report.
    """

    rule: Rule
    total: int
    passed: int
    failures: list[RoundTripFailure]

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        lines = [
            f"Round-trip report for {self.rule!r}",
            f"  samples: {self.total}, passed: {self.passed},"
            f" failed: {len(self.failures)}",
            "  Finite guarantee: only the checked samples are verified;",
            "  global equivalence is not claimed.",
        ]
        for failure in self.failures:
            lines.append(f"  [{failure.direction}] {failure.message}")
        return "\n".join(lines)


def check_converter_roundtrip(
    rule: Rule,
    values: t.Iterable[t.Any],
    *,
    compare: t.Callable[[t.Any, t.Any], bool] | None = None,
) -> RoundTripReport:
    """Check that ``to_url`` then ``to_python`` preserves identity for *rule*.

    For each finite sample value, the converter's ``to_url`` is called, the
    encoded value is checked against the converter's regex and path-segment
    boundaries, then ``to_python`` is called and the result is compared to the
    original value.

    This is a **finite sample check**: it only verifies the given values. It
    does not prove global equivalence. View functions are never called.

    :param rule: The rule containing the converter to check. It is bound to
        a map if it is not already.
    :param values: The finite sample values to check. For a rule with a single
        variable, scalars are accepted. For multiple variables, pass dicts.
    :param compare: Optional custom comparison. Called at most once per
        successful sample. Defaults to ``==``.
    """
    if rule.map is None:
        Map([rule])

    m = rule.map
    variables = sorted(rule.arguments)

    if not variables:
        raise ValueError(f"rule {rule!r} has no converter variables to check")

    single = len(variables) == 1

    if single:
        var = variables[0]
        samples: list[dict[str, t.Any]] = []
        for value in values:
            if isinstance(value, dict):
                samples.append(value)
            else:
                samples.append({var: value})
    else:
        samples = [dict(value) for value in values]

    failures: list[RoundTripFailure] = []
    passed = 0

    for sample in samples:
        sample_ok = True
        parsed: dict[str, t.Any] = {}

        for var in variables:
            original = sample[var]
            converter = rule._converters[var]

            # -- to_url direction ------------------------------------------
            try:
                encoded = converter.to_url(original)
            except Exception as exc:
                failures.append(
                    RoundTripFailure("to_url", original, f"{type(exc).__name__}: {exc}")
                )
                sample_ok = False
                continue

            # -- segment / regex check ------------------------------------
            if converter.part_isolating and "/" in encoded:
                failures.append(
                    RoundTripFailure(
                        "segment",
                        original,
                        f"encoded value {encoded!r} crosses a path segment",
                    )
                )
                sample_ok = False
                continue

            if re.fullmatch(converter.regex, encoded) is None:
                failures.append(
                    RoundTripFailure(
                        "segment",
                        original,
                        f"encoded value {encoded!r} does not match regex"
                        f" {converter.regex!r}",
                    )
                )
                sample_ok = False
                continue

            # -- to_python direction --------------------------------------
            try:
                parsed[var] = converter.to_python(encoded)
            except Exception as exc:
                failures.append(
                    RoundTripFailure(
                        "to_python", original, f"{type(exc).__name__}: {exc}"
                    )
                )
                sample_ok = False
                continue

        # -- full URL match check ------------------------------------------
        if sample_ok:
            try:
                built = rule.build(sample, append_unknown=False)
                if built is not None:
                    _, path = built
                    adapter = m.bind("example.org", "/")
                    matched_rule, _ = adapter.match(path, return_rule=True)
                    if matched_rule is not rule:
                        failures.append(
                            RoundTripFailure(
                                "segment",
                                sample,
                                f"built URL matched a different rule"
                                f" {matched_rule!r}, not {rule!r}",
                            )
                        )
                        sample_ok = False
            except RequestRedirect:
                # The URL matches but redirects (e.g. strict slashes). Still a
                # valid match for the rule.
                pass
            except Exception as exc:
                failures.append(
                    RoundTripFailure(
                        "segment",
                        sample,
                        f"built URL does not match rule: {type(exc).__name__}: {exc}",
                    )
                )
                sample_ok = False

        # -- identity check (compare at most once per sample) -------------
        if sample_ok:
            original_dict = {var: sample[var] for var in variables}
            if compare is not None:
                equal = compare(parsed, original_dict)
            else:
                equal = parsed == original_dict
            if not equal:
                failures.append(
                    RoundTripFailure(
                        "identity",
                        original_dict,
                        f"parsed {parsed!r} != original {original_dict!r}",
                    )
                )
                sample_ok = False

        if sample_ok:
            passed += 1

    return RoundTripReport(
        rule=rule, total=len(samples), passed=passed, failures=failures
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_integer_converter_12_roundtrips() -> None:
    """Integer converter: 12 finite samples all round-trip."""
    rule = Rule("/<int:a>", endpoint="a")
    values = [0, 1, 2, 3, 5, 7, 10, 42, 100, 123, 999, 1000]
    report = check_converter_roundtrip(rule, values)
    print(report.summary())
    assert report.ok, report.summary()
    assert report.passed == 12
    assert report.total == 12


def test_bad_segment_converter_fails() -> None:
    """A converter whose ``to_url`` crosses a path segment fails.

    This is the *bad converter* example: ``to_url`` returns a value with a
    slash, moving the encoded value into another path segment. The checker
    must report a ``segment`` failure.
    """

    class BadSegmentConverter(BaseConverter):
        regex = r"[^/]+"

        def to_python(self, value: str) -> t.Any:
            return value

        def to_url(self, value: t.Any) -> str:
            # Deliberately cross into another path segment.
            return f"{value}/extra"

    m = Map(
        [Rule("/<bad:a>", endpoint="a")],
        converters={"bad": BadSegmentConverter},
    )
    report = check_converter_roundtrip(m._rules_by_endpoint["a"][0], [1, 2, 3])
    print(report.summary())
    assert not report.ok
    assert report.passed == 0
    assert all(failure.direction == "segment" for failure in report.failures)


def test_no_view_calls() -> None:
    """The checker never calls view functions."""
    calls: list[int] = []

    def view() -> str:
        calls.append(1)
        return "ok"

    rule = Rule("/<int:a>", endpoint=view)
    report = check_converter_roundtrip(rule, [1, 2, 3])
    print(report.summary())
    assert report.ok, report.summary()
    assert calls == []


def test_compare_called_once_per_sample() -> None:
    """The custom compare runs at most once per successful sample."""
    calls: list[tuple[t.Any, t.Any]] = []

    def compare(parsed: t.Any, original: t.Any) -> bool:
        calls.append((parsed, original))
        return parsed == original

    rule = Rule("/<int:a>", endpoint="a")
    values = [1, 2, 3]
    report = check_converter_roundtrip(rule, values, compare=compare)
    assert report.ok, report.summary()
    assert len(calls) == 3


def test_directions_reported_separately() -> None:
    """Exceptions are reported by direction (``to_url`` vs ``to_python``)."""

    class BadToUrlConverter(BaseConverter):
        regex = r"[^/]+"

        def to_python(self, value: str) -> t.Any:
            return value

        def to_url(self, value: t.Any) -> str:
            raise ValidationError("bad to_url")

    m = Map(
        [Rule("/<bad:a>", endpoint="a")],
        converters={"bad": BadToUrlConverter},
    )
    rule = m._rules_by_endpoint["a"][0]
    report = check_converter_roundtrip(rule, [1])
    assert not report.ok
    assert report.failures[0].direction == "to_url"

    class BadToPythonConverter(BaseConverter):
        regex = r"[^/]+"

        def to_python(self, value: str) -> t.Any:
            raise ValidationError("bad to_python")

        def to_url(self, value: t.Any) -> str:
            return str(value)

    m = Map(
        [Rule("/<bad:a>", endpoint="a")],
        converters={"bad": BadToPythonConverter},
    )
    rule = m._rules_by_endpoint["a"][0]
    report = check_converter_roundtrip(rule, [1])
    assert not report.ok
    assert report.failures[0].direction == "to_python"


def test_finite_guarantee_in_report() -> None:
    """The report states the finite guarantee."""
    rule = Rule("/<int:a>", endpoint="a")
    report = check_converter_roundtrip(rule, [1])
    summary = report.summary()
    assert "Finite guarantee" in summary
    assert "global equivalence is not claimed" in summary
