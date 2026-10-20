# =============================================================================
# lib/errors.py: friendly error handling for a non-technical audience
# =============================================================================
# A business user must never see a Python traceback. Every failure becomes a
# plain-language message that says what went wrong and what to do. This wraps
# the Claude calls (bad key, rate limit, network) and any unexpected error.
# =============================================================================

import anthropic

from config import MODEL

# One plain line per failed model call, by the code narrate() records.
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


def model_failure_message(code):
    """The plain line for a failed model call, ending with its code."""
    text = MODEL_FAILURE_MESSAGES.get(code, MODEL_FAILURE_MESSAGES["service_unavailable"])
    return "{} Code: `{}`".format(text.format(model=MODEL), code)


def friendly_message(exc):
    """Turn an exception into a plain-language message for the user.
    Returns a short string. Never exposes a stack trace."""
    if isinstance(exc, anthropic.AuthenticationError):
        return ("That API key was not accepted. Check you pasted the whole "
                "key (it starts with sk-ant-) and that it is active in your "
                "Anthropic console, then try again.")

    if isinstance(exc, anthropic.RateLimitError):
        return ("Your account hit a rate limit. Wait a moment and try again. "
                "If it keeps happening, check your usage limits in the "
                "Anthropic console.")

    if isinstance(exc, anthropic.APIStatusError):
        return ("The AI service returned an error and could not complete the "
                "request. This is usually temporary. Please try again in a "
                "moment.")
    if isinstance(exc, anthropic.APIConnectionError):
        return ("Could not reach the AI service. Check your connection and "
                "try again.")

    if exc.__class__.__name__ == "NoKeyError":
        return ("Paste your Anthropic API key above to run a live analysis, "
                "or view the example output which needs no key.")

    return ("Something went wrong running that request. Please try again, or "
            "try one of the example prompts.")
