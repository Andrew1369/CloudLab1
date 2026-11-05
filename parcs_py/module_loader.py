"""Replacement for imp.load_source, removed in Python 3.12."""
import importlib.util
import sys


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module
