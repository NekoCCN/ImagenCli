"""Safe-by-default diagnostics; untrusted free text is never logged verbatim."""

import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, TextIO

_PRIVATE = re.compile(
    r"prompt|secret|token|key|signature|url|uri|response|base64|authorization|credential", re.I
)
# Usage counts are billing evidence, not credentials, even when named *Token*.
_USAGE = {
    "input_tokens", "output_tokens", "total_tokens",
    "promptTokenCount", "candidatesTokenCount", "totalTokenCount", "images",
}


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: (
                {name: redact(schema) for name, schema in item.items()}
                if key == "properties" and isinstance(item, Mapping)
                else "[redacted]"
                if str(key) not in _USAGE and _PRIVATE.search(str(key))
                else redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        if re.search(r"Bearer\s|https?://|data:image/|\x1b|(?:api_key|token|secret)=", value, re.I):
            return "[redacted]"
    return value


class RedactedFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Exception strings and logging arguments may contain arbitrary provider payloads.
        record.msg = "diagnostic event"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


@contextmanager
def diagnostic_context(stderr: TextIO) -> Iterator[logging.Logger]:
    handler = logging.StreamHandler(stderr)
    handler.addFilter(RedactedFilter())
    logger = logging.getLogger("imagen_cli")
    previous = logger.propagate
    logger.propagate = False
    logger.addHandler(handler)
    try:
        yield logger
    finally:
        logger.removeHandler(handler)
        logger.propagate = previous
