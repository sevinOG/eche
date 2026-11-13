# Default the fail-closed safety classifier to allow during unit tests.
# Production never reads this flag from config, persona, or user settings.
try:
    from core import classifier as _eche_classifier
    _eche_classifier.allow_in_tests(True)
except Exception:
    pass
