"""
Comprehensive edge case test values for all Python builtin types.

This module provides edge case values to stress test observational equivalence:
- Integer boundaries and special values
- Float special values (nan, inf, signed zeros, denormals)
- String edge cases (empty, unicode, very long)
- Collection edge cases (empty, nested, large)
- Boolean edge cases
- None and special constants
"""

import sys
import math
from typing import List, Dict, Any, Set, Tuple


class EdgeCaseValues:
    """Generate comprehensive edge case values for testing."""

    @staticmethod
    def integers() -> List[int]:
        """
        Edge case integers.

        Returns:
            List of integer edge cases
        """
        return [
            # Zero and signs
            0,
            -0,
            1,
            -1,
            # Small values
            2,
            -2,
            10,
            -10,
            # Boundaries
            sys.maxsize,
            -sys.maxsize - 1,
            # Large values (but not too large to cause issues)
            10**6,
            -(10**6),
            10**9,
            -(10**9),
            # Very large (bignum)
            10**100,
            -(10**100),
        ]

    @staticmethod
    def floats() -> List[float]:
        """
        Edge case floats.

        Returns:
            List of float edge cases
        """
        return [
            # Zero and signed zeros
            0.0,
            -0.0,
            # Normal values
            1.0,
            -1.0,
            0.5,
            -0.5,
            # Special values
            float("inf"),
            float("-inf"),
            float("nan"),
            # Very small (denormals/subnormals)
            sys.float_info.min,
            -sys.float_info.min,
            sys.float_info.epsilon,
            # Very large
            sys.float_info.max,
            -sys.float_info.max,
            # Common fractions that have representation issues
            0.1,
            0.2,
            0.3,
            # Math constants
            math.pi,
            math.e,
        ]

    @staticmethod
    def strings() -> List[str]:
        """
        Edge case strings.

        Returns:
            List of string edge cases
        """
        return [
            # Empty
            "",
            # Single character
            "a",
            "A",
            "0",
            " ",
            "\n",
            "\t",
            # Common strings
            "test",
            "hello",
            "Hello World",
            # With escape sequences
            "line1\nline2",
            "tab\there",
            "backslash\\test",
            "quote'test",
            'doublequote"test',
            # Unicode
            "🎉",  # Emoji
            "café",  # Accented
            "Hello, 世界",  # Mixed scripts
            "\u200b",  # Zero-width space
            "RTL: \u202etext",  # Right-to-left override
            # Long string
            "a" * 1000,
            # Pathological cases
            "\x00",  # Null byte (if handled correctly)
        ]

    @staticmethod
    def bytes_values() -> List[bytes]:
        """
        Edge case bytes.

        Returns:
            List of bytes edge cases
        """
        return [
            b"",
            b"a",
            b"test",
            b"hello world",
            b"\x00",  # Null byte
            b"\xff",  # Max byte value
            b"\x00\x01\x02",  # Sequential
            bytes(range(256)),  # All possible bytes
            b"a" * 1000,  # Long bytes
        ]

    @staticmethod
    def lists() -> List[Any]:
        """
        Edge case lists.

        Returns:
            List of list edge cases
        """
        return [
            # Empty
            [],
            # Single element
            [0],
            [1],
            ["a"],
            [None],
            # Multiple elements
            [1, 2, 3],
            [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
            ["a", "b", "c"],
            # Mixed types
            [1, "a", None, True],
            [0, "", False, []],
            # Nested
            [[]],
            [[1]],
            [[1, 2], [3, 4]],
            [[[]]],
            [[[[1]]]],
            # Large
            list(range(100)),
            list(range(1000)),
            # With duplicates
            [1, 1, 1],
            [0] * 100,
        ]

    @staticmethod
    def dicts() -> List[Dict[Any, Any]]:
        """
        Edge case dictionaries.

        Returns:
            List of dict edge cases
        """
        return [
            # Empty
            {},
            # Single entry
            {"a": 1},
            {0: "zero"},
            {"key": "value"},
            # Multiple entries
            {"a": 1, "b": 2},
            {"x": 10, "y": 20, "z": 30},
            {0: "a", 1: "b", 2: "c"},
            # Mixed key/value types
            {"str": 1, 2: "int", True: False},
            # Nested
            {"outer": {"inner": 1}},
            {"a": {}, "b": {}},
            {"nested": {"deeply": {"nested": "value"}}},
            # With None
            {"key": None},
            {None: "value"},  # None as key (valid in Python)
            # Large
            {f"key{i}": i for i in range(100)},
        ]

    @staticmethod
    def tuples() -> List[Tuple[Any, ...]]:
        """
        Edge case tuples.

        Returns:
            List of tuple edge cases
        """
        return [
            # Empty
            (),
            # Single element (note the comma!)
            (1,),
            ("a",),
            # Pairs (common in Python)
            (1, 2),
            ("a", "b"),
            (0, 1),
            # Multiple elements
            (1, 2, 3),
            (1, 2, 3, 4, 5),
            # Mixed types
            (1, "a", None),
            (0, "", False),
            # Nested
            ((),),
            ((1,),),
            ((1, 2), (3, 4)),
            # Large
            tuple(range(100)),
        ]

    @staticmethod
    def sets() -> List[Set[Any]]:
        """
        Edge case sets.

        Returns:
            List of set edge cases
        """
        return [
            # Empty
            set(),
            # Single element
            {1},
            {"a"},
            {0},
            # Multiple elements
            {1, 2, 3},
            {0, 1, 2, 3, 4},
            {"a", "b", "c"},
            # Large
            set(range(100)),
        ]

    @staticmethod
    def booleans() -> List[bool]:
        """
        Edge case booleans.

        Returns:
            List of boolean edge cases
        """
        return [True, False]

    @staticmethod
    def none_values() -> List[None]:
        """
        None values.

        Returns:
            List containing None
        """
        return [None]

    @classmethod
    def get_edge_cases_for_type(cls, type_hint: Any) -> List[Any]:
        """
        Get edge cases for a specific type.

        Args:
            type_hint: Type hint or name like 'int', 'str', 'list', etc.

        Returns:
            List of edge case values
        """
        type_str = str(type_hint).lower()

        if "int" in type_str:
            return cls.integers()
        elif "float" in type_str:
            return cls.floats()
        elif "str" in type_str:
            return cls.strings()
        elif "bytes" in type_str:
            return cls.bytes_values()
        elif "list" in type_str:
            return cls.lists()
        elif "dict" in type_str:
            return cls.dicts()
        elif "tuple" in type_str:
            return cls.tuples()
        elif "set" in type_str:
            return cls.sets()
        elif "bool" in type_str:
            return cls.booleans()
        else:
            # Default: mix of common types
            return [
                0,
                1,
                -1,
                "",
                "test",
                [],
                [1, 2, 3],
                {},
                {"key": "value"},
                True,
                False,
                None,
            ]

    @classmethod
    def get_comprehensive_test_values(cls) -> Dict[str, List[Any]]:
        """
        Get a comprehensive set of test values across all types.

        Returns:
            Dict mapping type name to edge case values
        """
        return {
            "int": cls.integers(),
            "float": cls.floats(),
            "str": cls.strings(),
            "bytes": cls.bytes_values(),
            "list": cls.lists(),
            "dict": cls.dicts(),
            "tuple": cls.tuples(),
            "set": cls.sets(),
            "bool": cls.booleans(),
            "none": cls.none_values(),
        }
