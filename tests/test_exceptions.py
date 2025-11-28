"""Tests for custom exception hierarchy in towel.unification.exceptions."""

import pytest
from towel.unification.exceptions import (
    TowelError,
    UnificationError,
    ExtractionError,
    OrphanVariableError,
    ScopeAnalysisError,
    RefactoringError,
    ParseError,
    ImportResolutionError,
    ClassHierarchyError,
)


class TestExceptionHierarchy:
    """Test the exception class hierarchy and inheritance."""

    def test_towel_error_is_base_exception(self):
        """TowelError should be the base for all custom exceptions."""
        assert issubclass(TowelError, Exception)

    def test_unification_error_inherits_from_towel_error(self):
        """UnificationError should inherit from TowelError."""
        assert issubclass(UnificationError, TowelError)
        assert issubclass(UnificationError, Exception)

    def test_extraction_error_inherits_from_towel_error(self):
        """ExtractionError should inherit from TowelError."""
        assert issubclass(ExtractionError, TowelError)
        assert issubclass(ExtractionError, Exception)

    def test_orphan_variable_error_inherits_from_extraction_error(self):
        """OrphanVariableError should inherit from ExtractionError."""
        assert issubclass(OrphanVariableError, ExtractionError)
        assert issubclass(OrphanVariableError, TowelError)
        assert issubclass(OrphanVariableError, Exception)

    def test_scope_analysis_error_inherits_from_towel_error(self):
        """ScopeAnalysisError should inherit from TowelError."""
        assert issubclass(ScopeAnalysisError, TowelError)
        assert issubclass(ScopeAnalysisError, Exception)

    def test_refactoring_error_inherits_from_towel_error(self):
        """RefactoringError should inherit from TowelError."""
        assert issubclass(RefactoringError, TowelError)
        assert issubclass(RefactoringError, Exception)

    def test_parse_error_inherits_from_towel_error(self):
        """ParseError should inherit from TowelError."""
        assert issubclass(ParseError, TowelError)
        assert issubclass(ParseError, Exception)

    def test_import_resolution_error_inherits_from_towel_error(self):
        """ImportResolutionError should inherit from TowelError."""
        assert issubclass(ImportResolutionError, TowelError)
        assert issubclass(ImportResolutionError, Exception)

    def test_class_hierarchy_error_inherits_from_towel_error(self):
        """ClassHierarchyError should inherit from TowelError."""
        assert issubclass(ClassHierarchyError, TowelError)
        assert issubclass(ClassHierarchyError, Exception)


class TestExceptionRaising:
    """Test that exceptions can be raised and caught correctly."""

    def test_towel_error_can_be_raised(self):
        """TowelError can be raised with a message."""
        with pytest.raises(TowelError, match="base error"):
            raise TowelError("base error")

    def test_unification_error_can_be_raised(self):
        """UnificationError can be raised with a message."""
        with pytest.raises(UnificationError, match="cannot unify"):
            raise UnificationError("cannot unify")

    def test_extraction_error_can_be_raised(self):
        """ExtractionError can be raised with a message."""
        with pytest.raises(ExtractionError, match="extraction failed"):
            raise ExtractionError("extraction failed")

    def test_orphan_variable_error_can_be_raised(self):
        """OrphanVariableError can be raised with a message."""
        with pytest.raises(OrphanVariableError, match="orphaned variable"):
            raise OrphanVariableError("orphaned variable")

    def test_scope_analysis_error_can_be_raised(self):
        """ScopeAnalysisError can be raised with a message."""
        with pytest.raises(ScopeAnalysisError, match="scope analysis failed"):
            raise ScopeAnalysisError("scope analysis failed")

    def test_refactoring_error_can_be_raised(self):
        """RefactoringError can be raised with a message."""
        with pytest.raises(RefactoringError, match="refactoring failed"):
            raise RefactoringError("refactoring failed")

    def test_parse_error_can_be_raised(self):
        """ParseError can be raised with a message."""
        with pytest.raises(ParseError, match="parse failed"):
            raise ParseError("parse failed")

    def test_import_resolution_error_can_be_raised(self):
        """ImportResolutionError can be raised with a message."""
        with pytest.raises(ImportResolutionError, match="import resolution failed"):
            raise ImportResolutionError("import resolution failed")

    def test_class_hierarchy_error_can_be_raised(self):
        """ClassHierarchyError can be raised with a message."""
        with pytest.raises(ClassHierarchyError, match="hierarchy error"):
            raise ClassHierarchyError("hierarchy error")


class TestExceptionCatching:
    """Test that exceptions can be caught by their base classes."""

    def test_unification_error_caught_as_towel_error(self):
        """UnificationError can be caught as TowelError."""
        with pytest.raises(TowelError):
            raise UnificationError("test")

    def test_extraction_error_caught_as_towel_error(self):
        """ExtractionError can be caught as TowelError."""
        with pytest.raises(TowelError):
            raise ExtractionError("test")

    def test_orphan_variable_error_caught_as_extraction_error(self):
        """OrphanVariableError can be caught as ExtractionError."""
        with pytest.raises(ExtractionError):
            raise OrphanVariableError("test")

    def test_orphan_variable_error_caught_as_towel_error(self):
        """OrphanVariableError can be caught as TowelError (multi-level inheritance)."""
        with pytest.raises(TowelError):
            raise OrphanVariableError("test")

    def test_scope_analysis_error_caught_as_towel_error(self):
        """ScopeAnalysisError can be caught as TowelError."""
        with pytest.raises(TowelError):
            raise ScopeAnalysisError("test")

    def test_refactoring_error_caught_as_towel_error(self):
        """RefactoringError can be caught as TowelError."""
        with pytest.raises(TowelError):
            raise RefactoringError("test")

    def test_parse_error_caught_as_towel_error(self):
        """ParseError can be caught as TowelError."""
        with pytest.raises(TowelError):
            raise ParseError("test")

    def test_import_resolution_error_caught_as_towel_error(self):
        """ImportResolutionError can be caught as TowelError."""
        with pytest.raises(TowelError):
            raise ImportResolutionError("test")

    def test_class_hierarchy_error_caught_as_towel_error(self):
        """ClassHierarchyError can be caught as TowelError."""
        with pytest.raises(TowelError):
            raise ClassHierarchyError("test")


class TestExceptionMessages:
    """Test that exception messages are preserved correctly."""

    def test_towel_error_preserves_message(self):
        """TowelError should preserve the error message."""
        try:
            raise TowelError("custom message")
        except TowelError as e:
            assert str(e) == "custom message"

    def test_unification_error_preserves_message(self):
        """UnificationError should preserve the error message."""
        try:
            raise UnificationError("unification failed for blocks")
        except UnificationError as e:
            assert str(e) == "unification failed for blocks"

    def test_orphan_variable_error_preserves_message(self):
        """OrphanVariableError should preserve the error message."""
        try:
            raise OrphanVariableError("variable 'x' would be orphaned")
        except OrphanVariableError as e:
            assert str(e) == "variable 'x' would be orphaned"

    def test_exception_with_no_message(self):
        """Exceptions can be raised without a message."""
        try:
            raise TowelError()
        except TowelError as e:
            assert str(e) == ""
