"""Explicit contract-based reading of :class:`MultiDict` query parameters.

The regular :meth:`MultiDict.get` behaviour silently takes the first
occurrence of a repeated key and falls back to a default when conversion
fails. :func:`read_contract` instead validates every key against a
declared :class:`Field` and either returns the complete result or raises a
single :class:`ContractError` that lists *all* violations.

    >>> from werkzeug.datastructures import ContractError
    >>> from werkzeug.datastructures import Field
    >>> from werkzeug.datastructures import MultiDict
    >>> from werkzeug.datastructures.contract import read_contract
    >>> args = MultiDict([("page", "2"), ("tag", "a"), ("tag", "b")])
    >>> result = read_contract(
    ...     args,
    ...     [
    ...         Field("page", converter=int),
    ...         Field("tag", multi=True, required=False),
    ...         Field("size", required=False, converter=int, default=10),
    ...     ],
    ... )
    >>> result == {"page": 2, "tag": ["a", "b"], "size": 10}
    True

Repeated single value keys are rejected (the first occurrence is not
silently taken), and a bad conversion produces no partial result:

    >>> try:
    ...     read_contract(MultiDict([("page", "bad")]), [Field("page", converter=int)])
    ... except ContractError as e:
    ...     print([(err.kind.value, err.occurrence) for err in e.errors])
    [('convert', 0)]

.. versionadded:: 3.2
"""

from __future__ import annotations

import collections.abc as cabc
import enum
import typing as t
from dataclasses import dataclass

if t.TYPE_CHECKING:
    from .structures import MultiDict

V = t.TypeVar("V")
T = t.TypeVar("T")


class EmptyPolicy(enum.StrEnum):
    """How an empty string (``""``) occurrence is handled.

    This only applies to values that *are* the empty string; a missing key
    and an actual ``None`` value have their own separate strategies.
    """

    REJECT = "reject"
    """Record an :attr:`ErrorKind.EMPTY` error for the occurrence."""

    DEFAULT = "default"
    """Use :attr:`Field.default` for a single value field, or skip the
    occurrence for a multi value field."""

    KEEP = "keep"
    """Keep the empty string as-is, without calling the converter."""


class NullPolicy(enum.StrEnum):
    """How an actual ``None`` occurrence is handled."""

    REJECT = "reject"
    """Record a :attr:`ErrorKind.NULL` error for the occurrence."""

    DEFAULT = "default"
    """Use :attr:`Field.default` for a single value field, or skip the
    occurrence for a multi value field."""

    KEEP = "keep"
    """Keep ``None`` as-is, without calling the converter."""


class ErrorKind(enum.StrEnum):
    """Why a single :class:`FieldError` was recorded."""

    MISSING = "missing"
    """The key was not present at all (``occurrence`` is ``None``)."""

    DUPLICATE = "duplicate"
    """A single value field received more than one occurrence."""

    EMPTY = "empty"
    """An empty string occurrence on a field that rejects them."""

    NULL = "null"
    """A ``None`` occurrence on a field that rejects them."""

    CONVERT = "convert"
    """The converter raised while converting an occurrence."""


@dataclass(frozen=True)
class FieldError:
    """One contract violation for one key.

    This is a value object attached to :attr:`ContractError.errors`, not an
    exception that is raised by itself.

    :param key: The key as declared on the :class:`Field`.
    :param kind: What went wrong, see :class:`ErrorKind`.
    :param occurrence: Zero-based index of the offending occurrence, or
        ``None`` when the key was missing entirely.
    :param value: The raw value that caused the error. For a missing key
        this is ``None``, which is distinct from a present value that
        itself *is* ``None`` (that reports :attr:`ErrorKind.NULL`).
    :param reason: The original exception from the converter, if any.
    """

    key: t.Any
    kind: ErrorKind
    occurrence: int | None = None
    value: t.Any = None
    reason: BaseException | None = None

    def __str__(self) -> str:
        if self.kind is ErrorKind.MISSING:
            return f"missing required key {self.key!r}"

        return (
            f"{self.kind.value} value for key {self.key!r} at occurrence"
            f" {self.occurrence}: {self.value!r}"
        )


class ContractError(ValueError):
    """Raised when one or more :class:`Field` declarations are not met.

    All violations are collected in :attr:`errors`, in declaration order of
    fields and occurrence order within a field. Nothing is returned and the
    source :class:`MultiDict` is not modified, so no caller can observe a
    partial result.
    """

    def __init__(self, errors: cabc.Sequence[FieldError]) -> None:
        self.errors: list[FieldError] = list(errors)
        super().__init__("; ".join(str(error) for error in self.errors))


@dataclass(frozen=True)
class Field(t.Generic[V, T]):
    """Declarative contract for one query parameter key.

    :param key: The key to look up in the :class:`MultiDict`.
    :param multi: When ``False`` (the default) the key must have exactly
        one occurrence; every further occurrence is a
        :attr:`ErrorKind.DUPLICATE` error instead of silently taking the
        first one. When ``True`` all occurrences are returned in order as a
        list.
    :param required: When ``True`` (the default) and the key is absent, an
        :attr:`ErrorKind.MISSING` error is recorded. A missing key is
        distinct from an empty string or a ``None`` occurrence.
    :param converter: Called exactly once per ordinary occurrence with the
        raw value. If it raises ``ValueError`` or ``TypeError``, an
        :attr:`ErrorKind.CONVERT` error is recorded.
    :param default: Value for an absent optional single value field, or for
        an empty/``None`` occurrence handled with the ``DEFAULT`` policy.
        For a multi value field it is used as-is when the key is absent
        (defaults to an empty list).
    :param empty: Strategy for empty string occurrences, see
        :class:`EmptyPolicy`. Defaults to :attr:`EmptyPolicy.REJECT`.
    :param null: Strategy for ``None`` occurrences, see
        :class:`NullPolicy`. Defaults to :attr:`NullPolicy.REJECT`.
    """

    key: t.Any
    multi: bool = False
    required: bool = True
    converter: cabc.Callable[[V], T] | None = None
    default: T | list[T] | None = None
    empty: EmptyPolicy = EmptyPolicy.REJECT
    null: NullPolicy = NullPolicy.REJECT

    def missing_default(self) -> t.Any:
        """Resolve the result when the key is absent but optional."""
        return [] if self.multi else self.default

    def special_default(self) -> t.Any:
        """Resolve the result for an empty/``None`` DEFAULT occurrence."""
        return self.default


def _convert_occurrences(
    spec: Field[t.Any, t.Any],
    raw: list[t.Any],
    errors: list[FieldError],
) -> list[t.Any]:
    """Convert kept occurrences in order, recording every failure."""
    converted: list[t.Any] = []

    for index, value in enumerate(raw):
        # An actual None is checked before the empty string check; the two
        # cases have deliberately separate policies and error kinds.
        if value is None:
            if spec.null is NullPolicy.REJECT:
                errors.append(FieldError(spec.key, ErrorKind.NULL, index, value))
            elif spec.null is NullPolicy.KEEP:
                converted.append(value)
            elif not spec.multi:
                # NullPolicy.DEFAULT: replace for single, skip for multi.
                converted.append(spec.special_default())

            continue

        if value == "":
            if spec.empty is EmptyPolicy.REJECT:
                errors.append(FieldError(spec.key, ErrorKind.EMPTY, index, value))
            elif spec.empty is EmptyPolicy.KEEP:
                converted.append(value)
            elif not spec.multi:
                # EmptyPolicy.DEFAULT: replace for single, skip for multi.
                converted.append(spec.special_default())

            continue

        if spec.converter is None:
            converted.append(value)
            continue

        try:
            # Exactly one call per ordinary occurrence.
            converted.append(spec.converter(value))
        except (ValueError, TypeError) as e:
            errors.append(FieldError(spec.key, ErrorKind.CONVERT, index, value, e))

    return converted


def read_contract(
    values: MultiDict[t.Any, V],
    fields: cabc.Mapping[t.Any, Field[V, t.Any]] | cabc.Iterable[Field[V, t.Any]],
) -> dict[t.Any, t.Any]:
    """Read a :class:`MultiDict` of query parameters by contract.

    Each key is declared once as a :class:`Field`: single or multi value,
    required or optional, with a converter. Every field is validated and
    converted, and the result is returned only when *all* fields pass;
    otherwise a single :class:`ContractError` holding every
    :class:`FieldError` is raised. Missing keys, empty strings and ``None``
    values follow separate strategies, the passed-in ``values`` object is
    never modified, multi value fields preserve occurrence order, and keys
    without a declared field are ignored.

    :param values: The parsed query parameters. It is not modified.
    :param fields: The fields to read, as a mapping of keys to
        :class:`Field` objects or an iterable of them.
    :return: A plain dict keyed by the declared field keys. Multi value
        fields always map to a list.
    :raises ContractError: if one or more fields cannot be satisfied.
    """
    if isinstance(fields, cabc.Mapping):
        specs: list[Field[t.Any, t.Any]] = list(fields.values())
    else:
        specs = list(fields)

    result: dict[t.Any, t.Any] = {}
    errors: list[FieldError] = []

    for spec in specs:
        raw = values.getlist(spec.key)

        if not raw:
            if spec.required:
                errors.append(FieldError(spec.key, ErrorKind.MISSING, None))
            else:
                result[spec.key] = spec.missing_default()

            continue

        if not spec.multi and len(raw) > 1:
            # Do not take the first one and "get away with it": report
            # every occurrence past the first, keeping its raw value.
            for index in range(1, len(raw)):
                errors.append(
                    FieldError(spec.key, ErrorKind.DUPLICATE, index, raw[index])
                )

            continue

        field_errors_before = len(errors)
        converted = _convert_occurrences(spec, raw, errors)

        if spec.multi:
            result[spec.key] = converted
        elif len(converted) == 1:
            # Use an explicit length check rather than truthiness so a
            # converted falsey value (e.g. ``int("0")``) is kept.
            result[spec.key] = converted[0]
        elif len(errors) == field_errors_before:
            # Single value whose only occurrence was replaced away by the
            # DEFAULT policy (the default itself may be ``None``).
            result[spec.key] = spec.special_default()
        # Otherwise the recorded error raises ContractError below.

    if errors:
        raise ContractError(errors)

    return result
