"""
Event schema validation using JSON Schema.
Validates payloads against registered event type schemas at ingestion time.
"""
import json
import logging
from typing import Any, Dict, Optional, Tuple

from jsonschema import Draft7Validator, FormatChecker, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EventType

logger = logging.getLogger("relora.schema_validator")


class SchemaValidator:
    """Validates webhook payloads against registered JSON schemas."""
    
    @staticmethod
    async def validate_payload(
        db: AsyncSession,
        project_id: str,
        event_type_name: str,
        payload: Dict[str, Any],
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate a payload against the registered schema for the event type.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Look up the event type schema
            from sqlalchemy import select
            result = await db.execute(
                select(EventType).where(
                    EventType.project_id == project_id,
                    EventType.name == event_type_name,
                    EventType.deprecated == False,
                )
            )
            event_type = result.scalar_one_or_none()
            
            if not event_type:
                # No schema registered for this event type - allow through
                logger.debug(f"No schema registered for event type: {event_type_name}")
                return True, None
            
            if not event_type.schema:
                # Event type exists but no schema defined - allow through
                logger.debug(f"Event type {event_type_name} has no schema defined")
                return True, None
            
            # Validate against the schema
            schema = event_type.schema
            validator = Draft7Validator(schema, format_checker=FormatChecker())
            
            errors = list(validator.iter_errors(payload))
            if errors:
                # Build a detailed error message
                error_details = []
                for error in errors:
                    path = " -> ".join(str(p) for p in error.path) if error.path else "root"
                    error_details.append(f"Path '{path}': {error.message}")
                
                error_message = f"Schema validation failed for event type '{event_type_name}': " + "; ".join(error_details)
                logger.warning(f"Schema validation failed: {error_message}")
                return False, error_message
            
            logger.info(f"Payload validated successfully against schema for event type: {event_type_name}")
            return True, None
            
        except Exception as e:
            logger.error(f"Error during schema validation: {e}", exc_info=True)
            # If validation fails due to system error, allow the payload through
            # to avoid blocking legitimate traffic due to validator bugs
            return True, None
    
    @staticmethod
    def validate_schema_definition(schema: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Validate that a schema definition is valid JSON Schema Draft 7.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            Draft7Validator.check_schema(schema)
            return True, None
        except Exception as e:
            return False, f"Invalid JSON Schema: {str(e)}"
    
    @staticmethod
    def get_schema_errors(payload: Dict[str, Any], schema: Dict[str, Any]) -> list:
        """
        Get detailed validation errors for a payload against a schema.
        
        Returns:
            List of error messages
        """
        validator = Draft7Validator(schema, format_checker=FormatChecker())
        errors = []
        for error in validator.iter_errors(payload):
            path = " -> ".join(str(p) for p in error.path) if error.path else "root"
            errors.append({
                "path": path,
                "message": error.message,
                "validator": error.validator,
                "validator_value": error.validator_value,
            })
        return errors
