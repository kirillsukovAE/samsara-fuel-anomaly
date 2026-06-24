import json
import sys

_corr_id = "<not set up>"
_is_json_out = False
_log_level = "INFO"

_level_order = ["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]


def setup_logger_once(params: dict, is_json_out=None, log_level=None):
    global _corr_id
    _corr_id = params["SamsaraFunctionCorrelationId"]

    global _is_json_out
    _is_json_out = (
        is_json_out
        if is_json_out is not None
        else params.get("SamsaraFunctionLoggerIsJsonOut", "False").lower() == "true"
    )

    global _log_level
    _log_level = (
        log_level
        if log_level is not None
        else params.get("SamsaraFunctionLoggerLevel", "INFO")
    )


def log(*args, **kwargs):
    level = "INFO"
    if "level" in kwargs:
        level = str(kwargs.pop("level")).upper()

    file = sys.stdout if level in ["INFO", "DEBUG"] else sys.stderr

    try:
        if _level_order.index(level) < _level_order.index(_log_level):
            return
    except ValueError:
        pass

    if _is_json_out:
        message = args[0] if (len(args) == 1 and not kwargs) else [*args, *kwargs]
        print(json.dumps({"correlation_id": _corr_id, "level": level, "message": message}), file=file)
        return

    prefix = f"{_corr_id} | " if level == "INFO" else f"{_corr_id} | {level} | "
    print(prefix, *args, **kwargs, file=file)
