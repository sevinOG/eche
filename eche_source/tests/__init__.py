# Default the fail-closed safety classifier to allow during unit tests.
# Production never reads this flag from config, persona, or user settings.
try:
    from core import classifier as _eche_classifier
    _eche_classifier.allow_in_tests(True)
except Exception:
    pass

# Hermetic checker routing: by default no local Ollama models are pulled and
# there is no xAI key, so a dev machine's Ollama or saved key never leaks in.
# Tests that need either patch these names explicitly.
try:
    _eche_classifier._ollama_local_models_real = _eche_classifier._ollama_local_models
    _eche_classifier._ollama_local_models = lambda: None
    _eche_classifier._xai_key_real = _eche_classifier._xai_key
    _eche_classifier._xai_key = lambda: ""
except Exception:
    pass
