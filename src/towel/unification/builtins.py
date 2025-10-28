"""
Python builtins tracking.

Tracks Python builtin functions and names that should never be treated
as free variables or parameterized.
"""

# Python builtin functions and constants
# These should never be parameterized or treated as free variables
PYTHON_BUILTINS = {
    # Common builtin functions
    'abs', 'all', 'any', 'ascii', 'bin', 'bool', 'bytearray', 'bytes',
    'callable', 'chr', 'classmethod', 'compile', 'complex', 'delattr',
    'dict', 'dir', 'divmod', 'enumerate', 'eval', 'exec', 'filter',
    'float', 'format', 'frozenset', 'getattr', 'globals', 'hasattr',
    'hash', 'help', 'hex', 'id', 'input', 'int', 'isinstance',
    'issubclass', 'iter', 'len', 'list', 'locals', 'map', 'max',
    'memoryview', 'min', 'next', 'object', 'oct', 'open', 'ord',
    'pow', 'print', 'property', 'range', 'repr', 'reversed', 'round',
    'set', 'setattr', 'slice', 'sorted', 'staticmethod', 'str', 'sum',
    'super', 'tuple', 'type', 'vars', 'zip',

    # Async iteration (Python 3.10+)
    'aiter', 'anext',

    # Debugging
    'breakpoint',

    # Builtin constants
    'True', 'False', 'None', 'Ellipsis', 'NotImplemented',

    # Interactive helpers (in REPL)
    'copyright', 'credits', 'exit', 'license', 'quit',

    # All builtin exceptions
    'ArithmeticError', 'AssertionError', 'AttributeError', 'BaseException',
    'BaseExceptionGroup', 'BlockingIOError', 'BrokenPipeError', 'BufferError',
    'BytesWarning', 'ChildProcessError', 'ConnectionAbortedError',
    'ConnectionError', 'ConnectionRefusedError', 'ConnectionResetError',
    'DeprecationWarning', 'EOFError', 'EncodingWarning', 'EnvironmentError',
    'Exception', 'ExceptionGroup', 'FileExistsError', 'FileNotFoundError',
    'FloatingPointError', 'FutureWarning', 'GeneratorExit', 'IOError',
    'ImportError', 'ImportWarning', 'IndentationError', 'IndexError',
    'InterruptedError', 'IsADirectoryError', 'KeyError', 'KeyboardInterrupt',
    'LookupError', 'MemoryError', 'ModuleNotFoundError', 'NameError',
    'NotADirectoryError', 'NotImplementedError', 'OSError', 'OverflowError',
    'PendingDeprecationWarning', 'PermissionError', 'ProcessLookupError',
    'PythonFinalizationError', 'RecursionError', 'ReferenceError',
    'ResourceWarning', 'RuntimeError', 'RuntimeWarning', 'StopAsyncIteration',
    'StopIteration', 'SyntaxError', 'SyntaxWarning', 'SystemError',
    'SystemExit', 'TabError', 'TimeoutError', 'TypeError', 'UnboundLocalError',
    'UnicodeDecodeError', 'UnicodeEncodeError', 'UnicodeError',
    'UnicodeTranslateError', 'UnicodeWarning', 'UserWarning', 'ValueError',
    'Warning', 'ZeroDivisionError',
}


def is_builtin(name: str) -> bool:
    """
    Check if a name is a Python builtin.

    Args:
        name: Variable/function name

    Returns:
        True if it's a builtin
    """
    return name in PYTHON_BUILTINS


def filter_builtins(names: set) -> set:
    """
    Filter out builtin names from a set of names.

    Args:
        names: Set of names

    Returns:
        Set with builtins removed
    """
    return {name for name in names if not is_builtin(name)}
