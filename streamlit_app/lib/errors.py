# =============================================================================
# lib/errors.py: friendly error handling for a non-technical audience
# =============================================================================
# A business user must never see a Python traceback. Every failure becomes a
# plain-language message that says what went wrong and what to do. This wraps
# the Claude calls (bad key, rate limit, network) and any unexpected error.
# =============================================================================

from config import MODEL
# The one mapping from exceptions to codes lives with the engine, which
# records the same code when narrate() falls back to the template.
from src.step5_narrate import failure_code

# One plain line per kind of failed model call, the same across the series. The
# short code goes into any audit record instead of an exception name.
MODEL_FAILURE_MESSAGES = {
    "key_rejected": "Anthropic did not accept this key. Check that it was copied in "
                    "full and has not been deleted or expired.",
    "key_forbidden": "This key is not allowed to use the model. Check the key's "
                     "permissions in your Anthropic account.",
    "model_not_found": "The model {model} could not be found. It may have been retired. "
                       "Please report this on the project's GitHub page.",
    "bad_request": "Anthropic rejected the request. This is a fault in this app, not in "
                   "your key. Please report it.",
    "rate_limited": "Anthropic is limiting requests on this key. Wait a minute and try "
                    "again, or check your plan limits.",
    "service_unavailable": "Anthropic could not be reached or is busy. This is usually "
                           "temporary, so try again in a moment.",
    "unreadable_reply": "The model replied, but not in the form this app asked for.",
}
KEY_LOOKS_COMPLETE = "Key looks complete. It is checked when you first run the model."


def model_failure_message(code):
    """The plain line for a failed model call, ending with its code."""
    text = MODEL_FAILURE_MESSAGES.get(code, MODEL_FAILURE_MESSAGES["service_unavailable"])
    return "{} Code: `{}`".format(text.format(model=MODEL), code)


def friendly_message(exc):
    """A plain sentence for any failure. Never a class name or a stack trace."""
    code = failure_code(exc)
    if code:
        return model_failure_message(code)
    if exc.__class__.__name__ == "NoKeyError":
        return ("Paste your Anthropic API key in the sidebar to run a live analysis, "
                "or open the worked example, which needs no key.")
    return "Something went wrong running that request. Please try again."
