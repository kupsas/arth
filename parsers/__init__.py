"""
Bank statement parsers and Gmail-derived parsers.

Subpackages:

- ``parsers.alerts`` — transaction alert emails (HTML).
- ``parsers.statements`` — statement PDFs from email.
- ``parsers.uploads`` — uploaded statement files (``PARSER_REGISTRY``).
- ``parsers.holdings`` — portfolio CSV/PDF (``HOLDING_PARSER_REGISTRY``).
- ``parsers.email_registry`` — Gmail sender → parser registry (``build_email_parser_registry``).

Import concrete modules directly (e.g. ``parsers.uploads``); avoid heavy imports from this
package root so optional dependencies do not create circular imports with ``api``.
"""
