from __future__ import annotations

import pytest

from werkzeug.routing import BaseConverter
from werkzeug.routing import BuildError
from werkzeug.routing import Map
from werkzeug.routing import Rule
from werkzeug.routing import ValidationError


def test_missing_argument_then_success() -> None:
    """Two rules for one endpoint: the first misses a value, the second
    builds. The diagnostics URL matches a normal build."""
    m = Map(
        [
            Rule("/user/<int:id>", endpoint="show"),
            Rule("/user/<string:slug>", endpoint="show"),
        ]
    )
    a = m.bind("example.com")

    diag = a.build_diagnostics("show", {"slug": "abc"})

    assert diag.succeeded
    assert diag.url == a.build("show", {"slug": "abc"}) == "/user/abc"
    assert len(diag.candidates) == 2

    first, second = diag.candidates
    assert first.rule.rule == "/user/<int:id>"
    assert not first.accepted
    assert not first.selected
    assert first.missing_arguments == ["id"]
    assert any("missing value for argument 'id'" in r for r in first.reasons)

    assert second.rule.rule == "/user/<string:slug>"
    assert second.accepted
    assert second.selected
    assert second.path == "/user/abc"

    text = diag.explain()
    assert "missing value for argument 'id'" in text
    assert "selected" in text
    assert "result: '/user/abc'" in text


def test_converter_rejection_reason() -> None:
    """A value that fails converter validation reports the converter's
    reason."""
    m = Map([Rule("/user/<int:id>", endpoint="show")])
    a = m.bind("example.com")

    diag = a.build_diagnostics("show", {"id": -5})

    assert not diag.succeeded
    assert diag.url is None
    (candidate,) = diag.candidates
    assert not candidate.accepted
    assert candidate.converter_errors == {"id": "Negative values are not allowed."}
    assert any("IntegerConverter" in r for r in candidate.reasons)
    assert "Negative values are not allowed." in diag.explain()


def test_method_rejected() -> None:
    """An unsupported method is listed as the rejection reason."""
    m = Map([Rule("/thing", methods=["POST"], endpoint="thing")])
    a = m.bind("example.com")

    diag = a.build_diagnostics("thing", method="DELETE")

    assert not diag.succeeded
    (candidate,) = diag.candidates
    assert candidate.allowed_methods == ("POST",)
    assert any("method 'DELETE' is not allowed" in r for r in candidate.reasons)
    assert "method 'DELETE' is not allowed" in diag.explain()


def test_default_method_fallback_passes() -> None:
    """Without an explicit method, the default method is tried first,
    then rules are tried without a method restriction, like build."""
    m = Map([Rule("/thing", methods=["POST"], endpoint="thing")])
    a = m.bind("example.com")

    diag = a.build_diagnostics("thing")

    assert diag.succeeded
    assert diag.url == a.build("thing") == "/thing"
    # one candidate per pass: default method GET, then no method
    assert [c.method for c in diag.candidates] == ["GET", None]
    assert diag.candidates[0].allowed_methods == ("POST",)
    assert diag.candidates[1].selected


def test_converter_called_once_per_candidate() -> None:
    """Converters with side effects are called at most once per
    candidate, and rendering the report does not call them again."""

    class CountingConverter(BaseConverter):
        regex = r"[0-9]+"
        calls = 0

        def to_url(self, value: object) -> str:
            type(self).calls += 1

            try:
                int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise ValidationError("not an int") from None

            return str(value)

    m = Map(
        [
            Rule("/a/<count:value>", endpoint="show"),
            Rule("/b/<count:value>", endpoint="show"),
        ],
        converters={"count": CountingConverter},
    )
    a = m.bind("example.com")

    CountingConverter.calls = 0
    diag = a.build_diagnostics("show", {"value": "xyz"})

    assert not diag.succeeded
    # one call per candidate, not one per render
    assert CountingConverter.calls == 2
    assert [c.converter_errors for c in diag.candidates] == [
        {"value": "not an int"},
        {"value": "not an int"},
    ]

    diag.explain()
    str(diag)
    diag.explain()
    assert CountingConverter.calls == 2

    # a second diagnostics run uses a fresh cache
    a.build_diagnostics("show", {"value": "xyz"})
    assert CountingConverter.calls == 4


def test_converter_called_once_on_success() -> None:
    """A rejected first candidate does not cause extra converter calls
    for the selected candidate."""

    class CountingConverter(BaseConverter):
        regex = r"[0-9]+"
        calls = 0

        def to_url(self, value: object) -> str:
            type(self).calls += 1
            return str(value)

    m = Map(
        [
            Rule("/user/<int:id>", endpoint="show"),
            Rule("/user/<count:slug>", endpoint="show"),
        ],
        converters={"count": CountingConverter},
    )
    a = m.bind("example.com")

    CountingConverter.calls = 0
    diag = a.build_diagnostics("show", {"slug": "42"})

    assert diag.succeeded
    # the first candidate is rejected for a missing value before any
    # converter runs, the second converts exactly once
    assert CountingConverter.calls == 1
    assert diag.url == a.build("show", {"slug": "42"}) == "/user/42"


def test_defaults_fill_and_mismatch() -> None:
    """Defaults fill missing values; a conflicting value is reported."""
    m = Map(
        [
            Rule("/all/", defaults={"page": 1}, endpoint="entries"),
            Rule("/all/page/<int:page>", endpoint="entries"),
        ]
    )
    a = m.bind("example.com")

    diag = a.build_diagnostics("entries")
    assert diag.succeeded
    assert diag.url == a.build("entries") == "/all/"
    assert diag.candidates[0].selected

    diag = a.build_diagnostics("entries", {"page": 2})
    assert diag.succeeded
    assert diag.url == a.build("entries", {"page": 2}) == "/all/page/2"
    first, second = diag.candidates
    assert first.mismatched_defaults == {"page": (2, 1)}
    assert any("does not match the rule default" in r for r in first.reasons)
    assert second.selected


def test_host_matching_constraint() -> None:
    """With host matching, a rule whose host differs is only a fallback."""
    m = Map(
        [
            Rule("/x", host="other.example.com", endpoint="x"),
            Rule("/x", host="example.com", endpoint="x"),
        ],
        host_matching=True,
    )
    a = m.bind("example.com")

    diag = a.build_diagnostics("x", force_external=True)

    assert diag.succeeded
    assert diag.url == a.build("x", force_external=True) == "http://example.com/x"
    first, second = diag.candidates
    assert first.host_mismatch
    assert not first.selected
    assert not first.fallback
    assert second.selected
    assert "host constraint: 'other.example.com'" in diag.explain()


def test_host_matching_fallback() -> None:
    """If no rule matches the host, the first built rule is the fallback."""
    m = Map([Rule("/x", host="other.example.com", endpoint="x")], host_matching=True)
    a = m.bind("example.com")

    diag = a.build_diagnostics("x")

    assert diag.succeeded
    assert diag.url == a.build("x") == "http://other.example.com/x"
    (candidate,) = diag.candidates
    assert candidate.selected
    assert candidate.fallback
    assert candidate.host_mismatch


def test_subdomain_constraint_shown() -> None:
    m = Map(
        [Rule("/", subdomain="kb", endpoint="kb/index")],
        default_subdomain="www",
    )
    a = m.bind("example.com", subdomain="kb")

    diag = a.build_diagnostics("kb/index")

    assert diag.succeeded
    assert diag.url == a.build("kb/index") == "/"
    assert "subdomain constraint: 'kb'" in diag.explain()


def test_unknown_endpoint() -> None:
    m = Map([Rule("/", endpoint="index")])
    a = m.bind("example.com")

    diag = a.build_diagnostics("missing")

    assert not diag.succeeded
    assert diag.candidates == []
    assert "no rules are registered" in diag.explain()


def test_query_args_match_build() -> None:
    m = Map([Rule("/search", endpoint="search")])
    a = m.bind("example.com")

    diag = a.build_diagnostics("search", {"q": "x y", "page": 2})

    assert diag.succeeded
    assert diag.url == a.build("search", {"q": "x y", "page": 2})
    assert diag.url is not None
    assert diag.url.startswith("/search?")


def test_build_error_compat() -> None:
    """Normal build behavior is unchanged: same BuildError, same
    attributes, and diagnostics never raises it."""
    m = Map([Rule("/user/<int:id>", endpoint="show")])
    a = m.bind("example.com")

    with pytest.raises(BuildError) as exc_info:
        a.build("show", {})

    e = exc_info.value
    assert isinstance(e, LookupError)
    assert e.endpoint == "show"
    assert e.values == {}
    assert e.method is None
    assert "Could not build url for endpoint 'show'" in str(e)

    m2 = Map([Rule("/thing", methods=["POST"], endpoint="thing")])
    a2 = m2.bind("example.com")

    with pytest.raises(BuildError) as exc_info:
        a2.build("thing", method="DELETE")

    assert exc_info.value.method == "DELETE"

    diag = a.build_diagnostics("show", {})
    assert not diag.succeeded
    assert diag.url is None
