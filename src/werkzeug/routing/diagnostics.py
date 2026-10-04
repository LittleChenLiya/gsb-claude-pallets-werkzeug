from __future__ import annotations

import typing as t
from dataclasses import dataclass
from dataclasses import field

if t.TYPE_CHECKING:
    from .rules import Rule


@dataclass
class BuildCandidate:
    """Diagnostics for a single rule that was considered while building a
    URL, in the order :meth:`.MapAdapter.build` considers it.

    Instances are created by :meth:`.MapAdapter.build_diagnostics` and
    should be treated as read-only.

    .. versionadded:: 3.2
    """

    rule: Rule
    """The rule that was considered."""

    method: str | None
    """The effective method the rule was checked against. This can differ
    from the requested method: if no method is given, the adapter's
    default method is tried first, then the rules are tried again without
    a method restriction.
    """

    reasons: list[str] = field(default_factory=list)
    """Human-readable reasons why the rule was rejected. Empty if the
    rule could build a URL.
    """

    missing_arguments: list[str] = field(default_factory=list)
    """Arguments required by the rule that were not provided by the given
    values or the rule's defaults.
    """

    mismatched_defaults: dict[str, tuple[t.Any, t.Any]] = field(default_factory=dict)
    """Arguments for which a provided value conflicts with the rule's
    default, mapping the argument name to ``(provided, default)``.
    """

    allowed_methods: tuple[str, ...] | None = None
    """The methods allowed by the rule, if the rule was rejected because
    of the method.
    """

    converter_errors: dict[str, str] = field(default_factory=dict)
    """Errors raised by converters while converting values, mapping the
    argument name to the error message.
    """

    domain: str | None = None
    """The domain part the rule built, if conversion succeeded."""

    path: str | None = None
    """The path part the rule built, if conversion succeeded."""

    host_mismatch: bool = False
    """Whether host matching is enabled and the rule built a host that
    does not match the current server name. Such a rule is only used as
    a fallback if no rule matches the host exactly.
    """

    selected: bool = False
    """Whether this candidate was selected to build the final URL."""

    fallback: bool = False
    """Whether this candidate was selected as the host mismatch fallback
    rather than an exact host match.
    """

    @property
    def accepted(self) -> bool:
        """Whether the rule was able to build a URL for the given values,
        regardless of whether it was selected.
        """
        return self.path is not None


@dataclass
class BuildDiagnostics:
    """The result of :meth:`.MapAdapter.build_diagnostics`.

    This is a read-only report, generating it does not change the map,
    the adapter, or the outcome of :meth:`.MapAdapter.build`.

    .. versionadded:: 3.2
    """

    endpoint: t.Any
    """The endpoint a URL was requested for."""

    values: dict[str, t.Any]
    """The normalized values that were used."""

    method: str | None
    """The method that was requested, or ``None`` to use the adapter's
    default method with a fallback to no method restriction.
    """

    candidates: list[BuildCandidate]
    """Every rule that was considered, in the order
    :meth:`.MapAdapter.build` considers them.
    """

    url: str | None
    """The URL that :meth:`.MapAdapter.build` would return, or ``None``
    if it would raise :class:`.BuildError`.
    """

    @property
    def succeeded(self) -> bool:
        """Whether a candidate was selected and a URL was built."""
        return self.url is not None

    def explain(self) -> str:
        """Render a human-readable explanation of how each candidate
        rule was evaluated and why the build succeeded or failed.
        """
        lines = [
            f"Build diagnostics for endpoint {self.endpoint!r}"
            f" (method {self.method!r}, values {self.values!r}):"
        ]

        if not self.candidates:
            lines.append("  no rules are registered for this endpoint")

        for i, candidate in enumerate(self.candidates, 1):
            if candidate.selected and candidate.fallback:
                status = "selected (host fallback)"
            elif candidate.selected:
                status = "selected"
            elif candidate.accepted:
                status = "accepted, not selected"
            else:
                status = "rejected"

            lines.append(f"  candidate {i} {candidate.rule!r}: {status}")
            lines.append(f"    evaluated with method {candidate.method!r}")

            rule = candidate.rule

            if rule.map is not None and rule.map.host_matching:
                lines.append(f"    host constraint: {rule.host!r}")
            elif (
                rule.map is not None and rule.map.subdomain_matching and rule.subdomain
            ):
                lines.append(f"    subdomain constraint: {rule.subdomain!r}")

            for reason in candidate.reasons:
                lines.append(f"    - {reason}")

            if candidate.host_mismatch:
                lines.append(
                    f"    - built host {candidate.domain!r} does not match the"
                    " current server name, only usable as a fallback"
                )

            if candidate.accepted:
                lines.append(
                    f"    - built domain {candidate.domain!r}"
                    f" and path {candidate.path!r}"
                )

        if self.url is None:
            lines.append(
                "result: no candidate could build a URL, build() would raise BuildError"
            )
        else:
            lines.append(f"result: {self.url!r}")

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.explain()
