"""
Unification-based code refactoring.

This module implements a principled approach to detecting and extracting
duplicate code using unification from type inference.
"""

from .refactor_engine import UnificationRefactorEngine

__all__ = ['UnificationRefactorEngine']
