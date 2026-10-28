"""Routes this build can dispatch; planning refuses the rest before approval.

Kept free of provider imports so an offline plan never touches an HTTP client.
``imagen_cli.execution.configured`` maps exactly these routes to adapters.
"""

__all__ = ["SUPPORTED_ROUTES"]

SUPPORTED_ROUTES = frozenset({("gemini", "generate_content"), ("openai", "images")})
