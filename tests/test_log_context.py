import io
import logging
import re

import log_context


def test_lines_carry_utc_date_session_and_case():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    log_context.install(handler)
    logger = logging.getLogger("storm.test.logctx")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        log_context.set_case("archive 2024-04-27")
        logger.warning("radar decode failed")
        log_context.set_case("live")
        logger.warning("again")
    finally:
        logger.removeHandler(handler)
    first, second = stream.getvalue().splitlines()
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}Z  WARNING ", first)
    assert f"[{log_context.SESSION_ID} archive 2024-04-27]" in first
    assert f"[{log_context.SESSION_ID} live]" in second
    assert re.fullmatch(r"[0-9a-f]{6}", log_context.SESSION_ID)
