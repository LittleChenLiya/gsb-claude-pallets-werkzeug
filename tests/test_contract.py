"""Tests for explicit contract reading of MultiDict query parameters.

Run just these tests locally (no Docker or full suite needed)::

    python -m pytest tests/test_contract.py -q

Run a single acceptance case::

    python -m pytest tests/test_contract.py -k duplicate -q
"""

from __future__ import annotations

import typing as t

import pytest

from werkzeug import datastructures as ds
from werkzeug.datastructures import ContractError
from werkzeug.datastructures import EmptyPolicy
from werkzeug.datastructures import ErrorKind
from werkzeug.datastructures import Field
from werkzeug.datastructures import FieldError
from werkzeug.datastructures import MultiDict
from werkzeug.datastructures import NullPolicy
from werkzeug.datastructures import read_contract


def test_three_field_example() -> None:
    """The three-parameter example: page (single, int), tag (multi),
    size (optional single with default)."""
    args = MultiDict([("page", "2"), ("tag", "a"), ("tag", "b")])

    result = read_contract(
        args,
        [
            Field("page", converter=int),
            Field("tag", multi=True, required=False),
            Field("size", required=False, converter=int, default=10),
        ],
    )

    assert result == {"page": 2, "tag": ["a", "b"], "size": 10}


def test_duplicate_single_value_is_rejected() -> None:
    """``page`` appearing twice for a single value field must be rejected,
    not silently resolved to the first occurrence."""
    args = MultiDict([("page", "1"), ("page", "2")])

    with pytest.raises(ContractError) as exc_info:
        read_contract(args, [Field("page", converter=int)])

    (error,) = exc_info.value.errors
    assert error == FieldError("page", ErrorKind.DUPLICATE, 1, "2")
    assert error.key == "page"
    assert error.occurrence == 1
    assert error.value == "2"


def test_duplicate_does_not_call_converter_and_keeps_first_only_raw() -> None:
    calls: list[str] = []

    def convert(value: str) -> int:
        calls.append(value)
        return int(value)

    args = MultiDict([("page", "1"), ("page", "2"), ("page", "3")])

    with pytest.raises(ContractError) as exc_info:
        read_contract(args, [Field("page", converter=convert)])

    # Structural failure: no half-converted data, converter untouched.
    assert calls == []
    assert [e.occurrence for e in exc_info.value.errors] == [1, 2]
    assert [e.value for e in exc_info.value.errors] == ["2", "3"]
    assert all(e.key == "page" for e in exc_info.value.errors)


def test_multi_value_preserves_order() -> None:
    args = MultiDict([("tag", "b"), ("tag", "a"), ("tag", "b")])

    result = read_contract(args, [Field("tag", multi=True, required=False)])

    assert result["tag"] == ["b", "a", "b"]


def test_multi_value_converts_each_in_order() -> None:
    args = MultiDict([("tag", "1"), ("tag", "2"), ("tag", "3")])

    result = read_contract(args, [Field("tag", multi=True, converter=int)])

    assert result["tag"] == [1, 2, 3]


def test_bad_conversion_has_no_half_result() -> None:
    """``page=bad`` fails; the otherwise valid ``tag`` must not leak out as
    a partial result."""
    args = MultiDict([("page", "bad"), ("tag", "a"), ("tag", "b")])

    with pytest.raises(ContractError) as exc_info:
        read_contract(
            args,
            [
                Field("page", converter=int),
                Field("tag", multi=True, required=False),
            ],
        )

    (error,) = exc_info.value.errors
    assert error.key == "page"
    assert error.kind is ErrorKind.CONVERT
    assert error.occurrence == 0
    assert error.value == "bad"
    assert isinstance(error.reason, ValueError)
    assert not hasattr(exc_info.value, "result")


def test_all_errors_collected_across_fields() -> None:
    args = MultiDict([("page", "bad"), ("page", "2")])

    with pytest.raises(ContractError) as exc_info:
        read_contract(
            args,
            [
                Field("page", converter=int),
                Field("missing"),
            ],
        )

    kinds = [(e.key, e.kind, e.occurrence) for e in exc_info.value.errors]
    assert ("page", ErrorKind.DUPLICATE, 1) in kinds
    assert ("missing", ErrorKind.MISSING, None) in kinds


def test_converter_called_exactly_once_per_item() -> None:
    calls: list[str] = []

    def convert(value: str) -> str:
        calls.append(value)
        return value.upper()

    args = MultiDict([("tag", "a"), ("tag", "b")])

    result = read_contract(args, [Field("tag", multi=True, converter=convert)])

    assert calls == ["a", "b"]
    assert result["tag"] == ["A", "B"]


def test_missing_required_key() -> None:
    with pytest.raises(ContractError) as exc_info:
        read_contract(MultiDict(), [Field("page", converter=int)])

    (error,) = exc_info.value.errors
    assert error == FieldError("page", ErrorKind.MISSING, None, None)
    assert str(error) == "missing required key 'page'"


def test_missing_optional_single_uses_default() -> None:
    result = read_contract(
        MultiDict(), [Field("page", required=False, converter=int, default=1)]
    )
    assert result == {"page": 1}


def test_missing_optional_single_default_is_none() -> None:
    result = read_contract(MultiDict(), [Field("page", required=False)])
    assert result == {"page": None}


def test_missing_optional_multi_is_empty_list() -> None:
    result = read_contract(MultiDict(), [Field("tag", multi=True, required=False)])
    assert result == {"tag": []}


def test_empty_string_is_distinct_from_missing_and_null() -> None:
    # Default policy REJECT: an empty string reports EMPTY, not MISSING.
    with pytest.raises(ContractError) as exc_info:
        read_contract(MultiDict([("q", "")]), [Field("q")])

    (error,) = exc_info.value.errors
    assert error.kind is ErrorKind.EMPTY
    assert error.occurrence == 0
    assert error.value == ""


def test_empty_string_keep_policy() -> None:
    result = read_contract(MultiDict([("q", "")]), [Field("q", empty=EmptyPolicy.KEEP)])
    assert result == {"q": ""}


def test_empty_string_default_policy_single() -> None:
    result = read_contract(
        MultiDict([("q", "")]),
        [Field("q", required=False, empty=EmptyPolicy.DEFAULT, default="x")],
    )
    assert result == {"q": "x"}


def test_empty_string_default_policy_multi_skips_occurrence() -> None:
    result = read_contract(
        MultiDict([("tag", "a"), ("tag", ""), ("tag", "b")]),
        [Field("tag", multi=True, required=False, empty=EmptyPolicy.DEFAULT)],
    )
    assert result["tag"] == ["a", "b"]


def test_null_value_is_distinct_case() -> None:
    with pytest.raises(ContractError) as exc_info:
        read_contract(MultiDict([("q", None)]), [Field("q")])  # type: ignore[list-item]

    (error,) = exc_info.value.errors
    assert error.kind is ErrorKind.NULL
    assert error.occurrence == 0
    assert error.value is None


def test_null_keep_and_default_policies() -> None:
    kept = read_contract(
        MultiDict([("q", None)]),  # type: ignore[list-item]
        [Field("q", null=NullPolicy.KEEP)],
    )
    assert kept == {"q": None}

    defaulted = read_contract(
        MultiDict([("q", None)]),  # type: ignore[list-item]
        [Field("q", required=False, null=NullPolicy.DEFAULT, default="x")],
    )
    assert defaulted == {"q": "x"}


def test_converter_not_called_for_special_values() -> None:
    def boom(value: t.Any) -> t.Any:
        raise AssertionError("converter must not run for special values")

    read_contract(
        MultiDict([("q", "")]), [Field("q", empty=EmptyPolicy.KEEP, converter=boom)]
    )
    read_contract(
        MultiDict([("q", None)]),  # type: ignore[list-item]
        [Field("q", null=NullPolicy.KEEP, converter=boom)],
    )


def test_source_multidict_is_not_modified() -> None:
    args = MultiDict([("page", "2"), ("tag", "a"), ("tag", "b")])
    before = args.to_dict(flat=False)

    read_contract(
        args,
        [
            Field("page", converter=int),
            Field("tag", multi=True, required=False),
        ],
    )

    assert args.to_dict(flat=False) == before
    assert args["page"] == "2"  # raw string still there
    assert args.getlist("tag") == ["a", "b"]


def test_undeclared_keys_are_ignored() -> None:
    args = MultiDict([("page", "2"), ("extra", "ignored")])
    result = read_contract(args, [Field("page", converter=int)])
    assert result == {"page": 2}


def test_mapping_form_of_fields() -> None:
    args = MultiDict([("page", "2")])
    result = read_contract(args, {"page": Field("page", converter=int)})
    assert result == {"page": 2}


def test_method_on_multidict() -> None:
    args = MultiDict([("page", "2"), ("tag", "a"), ("tag", "b")])
    result = args.read_contract(
        [
            Field("page", converter=int),
            Field("tag", multi=True, required=False),
        ]
    )
    assert result == {"page": 2, "tag": ["a", "b"]}


def test_method_on_combined_multidict() -> None:
    get = MultiDict([("page", "2")])
    post = MultiDict([("tag", "a"), ("tag", "b")])
    combined = ds.CombinedMultiDict([get, post])

    result = combined.read_contract(
        [
            Field("page", converter=int),
            Field("tag", multi=True, required=False),
        ]
    )
    assert result == {"page": 2, "tag": ["a", "b"]}


def test_get_behavior_is_unchanged() -> None:
    """The opt-in contract does not alter the legacy get semantics."""
    args = MultiDict([("page", "1"), ("page", "2"), ("bad", "x")])

    # Repeated key: first value, as before.
    assert args.get("page") == "1"
    assert args.get("page", type=int) == 1
    # Bad conversion falls back to default, as before.
    assert args.get("bad", 7, type=int) == 7
    # Missing key falls back to default / None, as before.
    assert args.get("missing") is None
    assert args.get("missing", 9) == 9
    assert args.getlist("page") == ["1", "2"]


def test_converted_falsey_value_is_kept() -> None:
    # int("0") is falsey; it must not be mistaken for "no occurrence".
    result = read_contract(MultiDict([("page", "0")]), [Field("page", converter=int)])
    assert result == {"page": 0}

    def falsey_convert(value: str) -> str:
        return ""

    kept = read_contract(
        MultiDict([("q", "x")]), [Field("q", converter=falsey_convert)]
    )
    assert kept == {"q": ""}


def test_empty_string_default_single_with_none_default() -> None:
    result = read_contract(
        MultiDict([("q", "")]), [Field("q", empty=EmptyPolicy.DEFAULT)]
    )
    assert result == {"q": None}


def test_field_error_strings_identify_key_and_occurrence() -> None:
    err = FieldError("page", ErrorKind.CONVERT, 0, "bad")
    assert "page" in str(err)
    assert "occurrence 0" in str(err)
    assert repr("bad") in str(err)
