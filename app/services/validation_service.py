"""Business Rules and Input Validation Engine."""

import re

from email_validator import EmailNotValidError, validate_email

from app.core.logging import logger
from app.models.enums import JobStatus


class InvalidStateTransitionError(Exception):
    """Raised when an illegal job state transition is attempted."""

    pass


class ValidationService:
    """Encapsulates validation rules for recipient records and job lifecycle states."""

    # Disallow control characters and purely punctuation names
    NAME_REGEX = re.compile(r"^[\w\s\.\,\'\-\u00C0-\u024F\u1E00-\u1EFF]{1,255}$", re.UNICODE)
    ID_REGEX = re.compile(r"^[A-Za-z0-9\-\_\.]{1,100}$")

    @classmethod
    def validate_recipient(
        cls, name: str, email: str, custom_identifier: str | None = None
    ) -> tuple[bool, str | None]:
        """
        Validates individual recipient data.
        Returns:
            (is_valid: bool, error_message: Optional[str])
        """
        # 1. Validate Name
        cleaned_name = name.strip() if name else ""
        if not cleaned_name:
            return False, "Recipient name cannot be empty or blank."
        if len(cleaned_name) > 255:
            return False, "Recipient name exceeds maximum allowed length of 255 characters."
        if not cls.NAME_REGEX.match(cleaned_name):
            return False, "Recipient name contains invalid characters."

        # 2. Validate Email
        cleaned_email = email.strip() if email else ""
        if not cleaned_email:
            return False, "Recipient email address is required."
        try:
            # Check syntax, deliverability check disabled for speed/offline tests
            validated = validate_email(cleaned_email, check_deliverability=False)
            cleaned_email = validated.normalized
        except EmailNotValidError as exc:
            return False, f"Invalid email format: {str(exc)}"

        # 3. Validate Custom Identifier (Optional)
        if custom_identifier:
            cleaned_id = custom_identifier.strip()
            if len(cleaned_id) > 100:
                return False, "Custom identifier exceeds maximum allowed length of 100 characters."
            if not cls.ID_REGEX.match(cleaned_id):
                return (
                    False,
                    "Custom identifier contains invalid characters (allowed: alphanumeric, -, _, .).",
                )

        return True, None

    @classmethod
    def check_and_flag_batch_duplicates(
        cls, recipients_data: list
    ) -> list[tuple[dict, bool, str | None]]:
        """
        Evaluates a batch of recipient dictionaries for duplicates and syntax errors.
        Returns a list of tuples: (recipient_dict, is_valid, error_reason).
        First occurrence is accepted; subsequent duplicates in same batch are flagged.
        """
        seen_emails: set[str] = set()
        evaluated_batch = []

        for item in recipients_data:
            name = item.get("name", "")
            email = item.get("email", "")
            custom_id = item.get("custom_identifier")

            # Validate basic field integrity
            is_valid, error_msg = cls.validate_recipient(name, email, custom_id)
            if not is_valid:
                evaluated_batch.append((item, False, error_msg))
                continue

            normalized_email = email.strip().lower()
            if normalized_email in seen_emails:
                msg = f"Duplicate recipient email '{normalized_email}' within the same batch."
                evaluated_batch.append((item, False, msg))
                continue

            seen_emails.add(normalized_email)
            evaluated_batch.append((item, True, None))

        return evaluated_batch

    @classmethod
    def validate_job_state_transition(
        cls, current_status: JobStatus, target_status: JobStatus
    ) -> None:
        """
        Guarantees that job status follows a strict, non-reversible state machine.
        Valid paths:
          PENDING -> PROCESSING
          PROCESSING -> COMPLETED | COMPLETED_WITH_ERRORS | FAILED
        """
        allowed_transitions = {
            JobStatus.PENDING: {JobStatus.PROCESSING, JobStatus.FAILED},
            JobStatus.PROCESSING: {
                JobStatus.COMPLETED,
                JobStatus.COMPLETED_WITH_ERRORS,
                JobStatus.FAILED,
            },
            JobStatus.COMPLETED: set(),
            JobStatus.COMPLETED_WITH_ERRORS: set(),
            JobStatus.FAILED: set(),
        }

        if target_status not in allowed_transitions.get(current_status, set()):
            err_msg = (
                f"Illegal state transition from '{current_status.value}' "
                f"to '{target_status.value}'."
            )
            logger.error(err_msg)
            raise InvalidStateTransitionError(err_msg)


validation_service = ValidationService()
