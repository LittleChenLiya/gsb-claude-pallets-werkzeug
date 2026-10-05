Data Structures
===============

.. module:: werkzeug.datastructures


MultiDict
---------

.. autoclass:: MultiDict
    :members:
    :inherited-members:

.. autoclass:: CombinedMultiDict


Contract-based reading
----------------------

.. versionadded:: 3.2

:meth:`MultiDict.get` silently takes the first occurrence of a repeated
key and falls back to a default when conversion fails. When the query
parameters are an explicit input contract, use
:func:`read_contract` (also available as :meth:`MultiDict.read_contract`)
with a :class:`Field` per key instead:

.. code-block:: python

    from werkzeug.datastructures import Field, read_contract

    result = read_contract(
        request.args,
        [
            Field("page", converter=int),
            Field("tag", multi=True, required=False),
            Field("size", required=False, converter=int, default=10),
        ],
    )

Rules enforced per field:

-   A single value field (``multi=False``) rejects a repeated key with an
    :attr:`ErrorKind.DUPLICATE` error instead of returning the first value.
-   A multi value field (``multi=True``) returns all occurrences in order.
-   Every ordinary occurrence calls ``converter`` exactly once; a
    :class:`ValueError` or :class:`TypeError` records an
    :attr:`ErrorKind.CONVERT` error.
-   Missing keys, empty strings and actual ``None`` values are separate
    cases. Missing required keys report :attr:`ErrorKind.MISSING`; empty
    strings follow :class:`EmptyPolicy`; ``None`` values follow
    :class:`NullPolicy`.
-   All fields are checked. The result is returned only when everything
    passes; otherwise a :class:`ContractError` with every
    :class:`FieldError` (key, occurrence index and raw value) is raised,
    so no partial result is observable.
-   The source :class:`MultiDict` is never modified, and keys without a
    declared field are ignored. The existing :meth:`MultiDict.get`
    behavior is unchanged.

.. autofunction:: read_contract

.. autoclass:: Field
    :members:

.. autoclass:: ContractError
    :members:

.. autoclass:: FieldError
    :members:

.. autoclass:: EmptyPolicy
    :members:

.. autoclass:: NullPolicy
    :members:

.. autoclass:: ErrorKind
    :members:


.. _http-datastructures:

HTTP Headers
------------

.. autoclass:: Headers
    :members:

.. autoclass:: EnvironHeaders


Structured Headers
------------------

These classes are used to parse and build specific headers for properties on
:class:`.Request` and :class:`.Response`. They all provide a ``from_header``
classmethod to create an instance by parsing the header string, and a
``to_header`` method to convert to a header string. ``bool`` can be used to test
if the instance is "empty", meaning the header value is missing or invalid. If
you need to test if the header is actually missing, look in
:attr:`.Request.headers` directly. ``to_header`` may return an empty string for
empty instances.

.. autoclass:: HeaderSet
    :members:

.. autoclass:: Accept
    :members:

.. autoclass:: MIMEAccept
    :members:

.. autoclass:: CharsetAccept

.. autoclass:: LanguageAccept

.. autoclass:: RequestCacheControl
    :members:
    :member-order: groupwise

.. autoclass:: ResponseCacheControl
    :members:
    :member-order: groupwise

.. autoclass:: Authorization
    :members:

.. autoclass:: WWWAuthenticate
    :members:

.. autoclass:: ContentSecurityPolicy
    :members:

.. autoclass:: ETag
    :members:

.. autoclass:: ETagSet
    :members:

    .. automethod:: __call__

.. autoclass:: IfRange
    :members:

.. autoclass:: Range
    :members:

.. autoclass:: ContentRange
    :members:


File Uploads
------------

.. autoclass:: FileStorage
    :members:

.. autoclass:: FileMultiDict
    :members:


Deprecated Utilities
--------------------

.. autoclass:: TypeConversionDict
    :members:

.. autoclass:: ImmutableTypeConversionDict
    :members: copy

.. autoclass:: ImmutableDict
    :members: copy

.. autoclass:: ImmutableList
