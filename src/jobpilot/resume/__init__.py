"""Facts-only resume loading, rendering, and validation."""

from .engine import (
    BaseResume,
    ResumeValidationError,
    ValidationReport,
    extract_source_text,
    load_base_resume,
    render_docx,
    unverified_facts,
    validate_pdf,
    validate_docx,
)

__all__ = [
    "BaseResume",
    "ResumeValidationError",
    "ValidationReport",
    "extract_source_text",
    "load_base_resume",
    "render_docx",
    "unverified_facts",
    "validate_docx",
    "validate_pdf",
]
