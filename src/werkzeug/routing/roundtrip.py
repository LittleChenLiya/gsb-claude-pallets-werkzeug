"""Explicit round-trip checker for rule converters.

For a *finite* set of sample values, verify that a converter's
``to_url`` followed by ``to_python`` preserves the declared identity of
the value::

    value --to_url--> url segment --to_python--> value (unchanged)

The checker enforces, per sample:

- ``to_url`` must succeed (encode direction).
- The encoded segment must not cross path segments: a ``/`` in the
  output of a part-isolating converter is a failure.
- ``to_python`` must succeed on the encoded segment (decode direction).
- The encoded path must still match the *original* rule (not another
  rule, and no routing exception).
- A comparison function must confirm the decoded value keeps the
  identity of the sample. It is called **at most once per successful
  sample**.

The checker never calls view functions; it only exercises converters
and the URL matcher. Failures are reported separately per direction
(``to_url`` / ``segment`` / ``to_python`` / ``match`` / ``compare``).

.. important::

    This is a *finite* guarantee. Only the provided samples are
    checked. A passing report does **not** claim the converter
    round-trips for every possible value.

Example of a deliberately bad converter whose ``to_url`` produces
another path segment, which the checker rejects::

    class BadIntConverter(IntegerConverter):
        def to_url(self, value):
            return f"{int(value)}/extra"  # crosses a path segment

Run the targeted tests for this checker with::

    python -m pytest tests/test_routing_roundtrip.py -q
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass
from dataclasses import field
from urllib.parse import quote

from ..exceptions import HTTPException
from .rules import Rule

#: Statement of the finite guarantee, included in every report summary.
FINITE_GUARANTEE = (
    "Finite guarantee: only the {total} explicitly provided sample(s) "
    "were checked. This does NOT claim the converter round-trips for "
    "every possible value; no universal equivalence is asserted."
)

#: Targeted pytest command to re-run only this checker's tests.
PYTEST_COMMAND = "python -m pytest tests/test_routing_roundtrip.py -q"

#: Example of a bad converter that fails the round-trip check.
BAD_CONVERTER_EXAMPLE = """\
class BadIntConverter(IntegerConverter):
    def to_url(self, value):
        return f"{int(value)}/extra"  # crosses a path segment
"""

#: Failure directions, reported separately.
DIRECTIONS = ("to_url", "segment", "to_python", "match", "compare")


@dataclass
class RoundTripFailure:
    """A single sample that failed the round-trip check.

    :param sample: The sample argument mapping that failed.
    :param direction: Which stage failed: ``"to_url"``, ``"segment"``,
        ``"to_python"``, ``"match"``, or ``"compare"``.
    :param message: A human-readable description.
    :param exception: The exception that caused the failure, if any.
    """

    sample: dict[str, t.Any]
    direction: str
    message: str
    exception: BaseException | None = None


@dataclass
class RoundTripReport:
    """Result of :func:`check_roundtrip` over a finite set of samples.

    Only the checked samples are covered by this report; it does not
    claim universal equivalence (see :data:`FINITE_GUARANTEE`).
    """

    rule: Rule
    total: int
    passed: list[dict[str, t.Any]] = field(default_factory=list)
    failures: list[RoundTripFailure] = field(default_factory=list)
    compare_calls: int = 0
    view_calls: int = 0

    @property
    def ok(self) -> bool:
        """``True`` if every sample round-tripped successfully."""
        return not self.failures

    def failures_for(self, direction: str) -> list[RoundTripFailure]:
        """Return only the failures for one direction."""
        return [f for f in self.failures if f.direction == direction]

    def summary(self) -> str:
        """A human-readable report, including the finite guarantee."""
        lines = [
            f"Round-trip report for {self.rule!r}",
            f"samples checked: {self.total}"
            f", passed: {len(self.passed)}"
            f", failed: {len(self.failures)}",
        ]

        for direction in DIRECTIONS:
            count = len(self.failures_for(direction))

            if count:
                lines.append(f"  {direction} failures: {count}")

        lines.append(f"compare function calls: {self.compare_calls}")
        lines.append(f"view function calls: {self.view_calls}")
        lines.append(FINITE_GUARANTEE.format(total=self.total))
        lines.append(f"targeted re-check: {PYTEST_COMMAND}")
        return "\n".join(lines)


def _default_compare(decoded: t.Any, original: t.Any) -> bool:
    return decoded == original


def _build_path(rule: Rule, encoded: t.Mapping[str, str]) -> str:
    """Build the URL path for ``rule`` from already-encoded segments.

    Mirrors how :meth:`Rule._compile_builder` assembles the path, but
    uses the provided encoded values instead of calling ``to_url``.
    """
    parts: list[str] = []
    in_path = False

    for is_dynamic, data in rule._trace:
        if not in_path:
            if data == "|":
                in_path = True

            continue

        if is_dynamic:
            parts.append(encoded[data])
        else:
            # safe = https://url.spec.whatwg.org/#url-path-segment-string
            parts.append(quote(data, safe="!$&'()*+,/:;=@"))

    return "".join(parts)


def check_roundtrip(
    rule: Rule,
    samples: t.Iterable[t.Any],
    compare: t.Callable[[t.Any, t.Any], bool] | None = None,
    method: str = "GET",
) -> RoundTripReport:
    """Check a finite set of samples round-trip through a rule's converters.

    For each sample, the converter's ``to_url`` output must not cross a
    path segment, must still match the original rule, and its
    ``to_python`` result must preserve the sample's identity according
    to ``compare``. View functions are never called.

    :param rule: A bound :class:`~werkzeug.routing.Rule` to check.
    :param samples: Finite iterable of samples. Each sample is a dict
        mapping argument names to values, or a bare value if the rule
        has exactly one argument.
    :param compare: ``compare(decoded, original) -> bool``. Called at
        most once per successful sample. For single-argument rules it
        receives the two values, otherwise the two argument dicts.
        Defaults to ``==``.
    :param method: HTTP method used when matching the encoded path.
    :return: A :class:`RoundTripReport`. The report only covers the
        given samples; it does not claim universal equivalence.

    .. versionadded:: 3.2
    """
    if rule.map is None:
        raise ValueError("rule must be bound to a Map")

    if compare is None:
        compare = _default_compare

    arguments = sorted(rule.arguments)
    normalized: list[dict[str, t.Any]] = []

    for sample in samples:
        if isinstance(sample, t.Mapping):
            item = dict(sample)
        else:
            if len(arguments) != 1:
                raise TypeError(
                    f"samples must be dicts for rules with {len(arguments)} arguments"
                )

            item = {arguments[0]: sample}

        missing = [name for name in arguments if name not in item]

        if missing:
            raise ValueError(f"sample {item!r} is missing arguments {missing!r}")

        normalized.append(item)

    adapter = rule.map.bind("example.com")
    report = RoundTripReport(rule=rule, total=len(normalized))

    for sample in normalized:
        # Encode direction: to_url.
        encoded: dict[str, str] = {}
        encode_failure: RoundTripFailure | None = None

        for name in arguments:
            try:
                encoded[name] = rule._converters[name].to_url(sample[name])
            except Exception as e:
                encode_failure = RoundTripFailure(
                    sample=sample,
                    direction="to_url",
                    message=(
                        f"to_url({sample[name]!r}) for {name!r} raised"
                        f" {type(e).__name__}: {e}"
                    ),
                    exception=e,
                )
                break

        if encode_failure is not None:
            report.failures.append(encode_failure)
            continue

        # The encoded segment must not cross path segments.
        crossed = [
            name
            for name in arguments
            if rule._converters[name].part_isolating and "/" in encoded[name]
        ]

        if crossed:
            report.failures.append(
                RoundTripFailure(
                    sample=sample,
                    direction="segment",
                    message=(
                        f"to_url output for {crossed!r} contains '/' and"
                        " crosses a path segment:"
                        f" {[encoded[name] for name in crossed]!r}"
                    ),
                )
            )
            continue

        # Decode direction: to_python.
        decoded: dict[str, t.Any] = {}
        decode_failure: RoundTripFailure | None = None

        for name in arguments:
            try:
                decoded[name] = rule._converters[name].to_python(encoded[name])
            except Exception as e:
                decode_failure = RoundTripFailure(
                    sample=sample,
                    direction="to_python",
                    message=(
                        f"to_python({encoded[name]!r}) for {name!r} raised"
                        f" {type(e).__name__}: {e}"
                    ),
                    exception=e,
                )
                break

        if decode_failure is not None:
            report.failures.append(decode_failure)
            continue

        # The encoded path must still match the original rule. This
        # only runs the URL matcher; no view function is called.
        path = _build_path(rule, encoded)

        try:
            matched_rule, _ = adapter.match(
                path, method=method, return_rule=True, websocket=rule.websocket
            )
        except HTTPException as e:
            report.failures.append(
                RoundTripFailure(
                    sample=sample,
                    direction="match",
                    message=(
                        f"encoded path {path!r} does not match {rule!r}:"
                        f" {type(e).__name__}: {e}"
                    ),
                    exception=e,
                )
            )
            continue

        if matched_rule is not rule:
            report.failures.append(
                RoundTripFailure(
                    sample=sample,
                    direction="match",
                    message=(
                        f"encoded path {path!r} matched {matched_rule!r}"
                        f" instead of {rule!r}"
                    ),
                )
            )
            continue

        # Identity check: compare is called at most once per sample.
        report.compare_calls += 1

        if len(arguments) == 1:
            name = arguments[0]
            identical = compare(decoded[name], sample[name])
        else:
            identical = compare(decoded, sample)

        if not identical:
            report.failures.append(
                RoundTripFailure(
                    sample=sample,
                    direction="compare",
                    message=(
                        f"round trip did not preserve identity:"
                        f" {sample!r} decoded to {decoded!r}"
                    ),
                )
            )
            continue

        report.passed.append(sample)

    return report
