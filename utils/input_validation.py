"""
Input Validation Utilities
Comprehensive input validation and sanitization
"""

import re
import logging
from typing import Union
from decimal import Decimal, InvalidOperation
import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat
from utils.exception_handler import ValidationError

logger = logging.getLogger(__name__)


class InputValidator:
    """Comprehensive input validation"""

    # Regular expressions for validation
    USERNAME_PATTERN = re.compile(r"^@[a-z0-9_]{4,32}$")
    EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
    CRYPTO_ADDRESS_PATTERNS = {
        "BTC": re.compile(r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$|^bc1[a-z0-9]{39,59}$"),
        "ETH": re.compile(r"^0x[a-fA-F0-9]{40}$"),
        "TRX": re.compile(r"^T[A-Za-z1-9]{33}$"),
        "LTC": re.compile(r"^[LM3][a-km-zA-HJ-NP-Z1-9]{26,33}$"),
        "DOGE": re.compile(r"^D{1}[5-9A-HJ-NP-U]{1}[1-9A-HJ-NP-Za-km-z]{32}$"),
        "BCH": re.compile(
            r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$|^bitcoincash:[a-z0-9]{42}$"
        ),
    }

    @classmethod
    def validate_username(cls, username: str) -> str:
        """Validate and sanitize Telegram username"""
        if not username:
            raise ValidationError("Username cannot be empty")

        # Clean input
        username = username.strip()

        # Add @ if missing
        if not username.startswith("@"):
            username = "@" + username

        # Normalize to lowercase (Telegram requirement)
        username = username.lower()

        # Validate format
        if not cls.USERNAME_PATTERN.match(username):
            # Provide helpful, specific feedback
            if len(username) < 5:  # @ + 4 chars minimum
                raise ValidationError(
                    "Username too short. Try: @john or @user123 (minimum 4 characters after @)"
                )
            elif len(username) > 33:  # @ + 32 chars maximum
                raise ValidationError(
                    "Username too long. Maximum 32 characters after @ (currently: {})".format(len(username) - 1)
                )
            else:
                raise ValidationError(
                    "Username must contain only lowercase letters, numbers, and underscores.\nExamples: @john_doe, @alice2024, @trading_pro"
                )

        return username

    @classmethod
    def validate_amount(
        cls, amount_str: str, min_amount: float = 0.01, max_amount: float = 100000.0
    ) -> Decimal:
        """Validate and convert amount to Decimal"""
        if not amount_str:
            raise ValidationError("Amount cannot be empty")

        # Clean input
        amount_str = amount_str.strip().replace(",", "").replace("$", "")

        try:
            amount = Decimal(amount_str)
        except InvalidOperation:
            raise ValidationError("Invalid amount format. Please enter a valid number")

        if amount < Decimal(str(min_amount)):
            raise ValidationError(f"Amount must be at least ${min_amount}")

        if amount > Decimal(str(max_amount)):
            raise ValidationError(f"Amount cannot exceed ${max_amount}")

        # Check decimal places (max 2 for USD)
        exponent = amount.as_tuple().exponent
        if isinstance(exponent, int) and exponent < -2:
            raise ValidationError("Amount cannot have more than 2 decimal places")

        return amount

    @classmethod
    def validate_crypto_address(cls, address: str, currency: str) -> str:
        """Validate cryptocurrency address format"""
        if not address:
            raise ValidationError("Address cannot be empty")

        address = address.strip()

        # Get pattern for currency
        pattern = cls.CRYPTO_ADDRESS_PATTERNS.get(currency.upper())
        if not pattern:
            # For USDT variants, use ETH or TRX patterns
            if "ERC20" in currency.upper():
                pattern = cls.CRYPTO_ADDRESS_PATTERNS["ETH"]
            elif "TRC20" in currency.upper():
                pattern = cls.CRYPTO_ADDRESS_PATTERNS["TRX"]
            else:
                # Generic validation - just check it's not empty and reasonable length
                if len(address) < 10 or len(address) > 100:
                    raise ValidationError("Invalid address format")
                return address

        if not pattern.match(address):
            raise ValidationError(f"Invalid {currency} address format")

        return address

    @classmethod
    def validate_email(cls, email: str) -> str:
        """Validate email address format with comprehensive checks"""
        if not email:
            raise ValidationError("Email cannot be empty")
        
        # Clean input
        email = email.strip().lower()
        
        # Length validation per RFC 5321
        if len(email) > 254:
            raise ValidationError("Email address too long (maximum 254 characters)")
        
        # Must contain @
        if "@" not in email:
            raise ValidationError(
                "Invalid email format - missing @ symbol\n"
                "Examples: user@example.com, contact@gmail.com"
            )
        
        # Split and validate parts
        try:
            local_part, domain_part = email.rsplit("@", 1)
        except ValueError:
            raise ValidationError("Invalid email format")
        
        # Validate local part (before @)
        if not local_part or len(local_part) > 64:
            raise ValidationError("Email username part is invalid (max 64 characters before @)")
        
        if local_part.startswith(".") or local_part.endswith("."):
            raise ValidationError("Email cannot start or end with a dot")
        
        if ".." in local_part:
            raise ValidationError("Email cannot contain consecutive dots")
        
        # Validate domain part (after @)
        if not domain_part or len(domain_part) > 255:
            raise ValidationError("Email domain is invalid (max 255 characters after @)")
        
        if "." not in domain_part:
            raise ValidationError(
                "Invalid email domain - missing dot\n"
                "Example: user@example.com (not user@examplecom)"
            )
        
        if domain_part.startswith(".") or domain_part.endswith("."):
            raise ValidationError("Email domain cannot start or end with a dot")
        
        if ".." in domain_part:
            raise ValidationError("Email domain cannot contain consecutive dots")
        
        # Validate each domain label (RFC 1035)
        domain_labels = domain_part.split(".")
        for label in domain_labels:
            if not label:
                raise ValidationError("Email domain contains empty label")
            
            if len(label) > 63:
                raise ValidationError(
                    f"Email domain label too long: '{label}' exceeds 63 characters"
                )
            
            if label.startswith("-") or label.endswith("-"):
                raise ValidationError(
                    f"Email domain label invalid: '{label}' cannot start or end with hyphen"
                )
            
            # Check for valid characters (letters, numbers, hyphens only)
            if not re.match(r'^[a-z0-9-]+$', label):
                raise ValidationError(
                    f"Email domain label invalid: '{label}' contains illegal characters"
                )
        
        # Validate domain extension (TLD) is at least 2 characters
        tld = domain_part.split(".")[-1]
        if len(tld) < 2:
            raise ValidationError("Email domain extension too short (e.g., .com, .org)")
        
        # Final pattern match for allowed characters
        if not cls.EMAIL_PATTERN.match(email):
            raise ValidationError(
                "Invalid email format - use only letters, numbers, dots, and basic symbols\n"
                "Examples: user@example.com, contact_123@gmail.com"
            )
        
        return email

    @classmethod
    def validate_phone(cls, phone: str) -> str:
        """Validate phone number format with country code requirement"""
        
        if not phone:
            raise ValidationError("Phone number cannot be empty")
        
        phone = phone.strip()
        
        # Enforce + prefix for country code
        if not phone.startswith("+"):
            raise ValidationError(
                "Phone number must start with + and country code\n"
                "Examples: +12025551234, +447700900123, +234812345678"
            )
        
        try:
            # Parse without region hint (expects E.164 format with country code)
            parsed_number = phonenumbers.parse(phone, None)
            
            # Validate number is possible and valid
            if not phonenumbers.is_possible_number(parsed_number):
                raise ValidationError(
                    "Invalid phone number format—please double-check the digits"
                )
            
            if not phonenumbers.is_valid_number(parsed_number):
                raise ValidationError(
                    "Invalid phone number—please verify the country code and number"
                )
            
            # Return normalized E.164 format
            return phonenumbers.format_number(parsed_number, PhoneNumberFormat.E164)
            
        except NumberParseException:
            raise ValidationError(
                "Invalid phone number format. Use + followed by country code and number\n"
                "Examples: +12025551234, +447700900123"
            )

    @classmethod
    def validate_description(cls, description: str, max_length: int = 500) -> str:
        """Validate and sanitize description text"""
        if not description:
            raise ValidationError("Description cannot be empty")

        description = description.strip()

        if len(description) < 10:
            raise ValidationError("Description must be at least 10 characters")

        if len(description) > max_length:
            raise ValidationError(f"Description cannot exceed {max_length} characters")

        # Basic content filtering
        prohibited_patterns = [
            r"(?i)(hack|crack|exploit|illegal|fraud|scam)",
            r"(?i)(drug|weapon|gambling|porn)",
            r"(?i)(\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9})",  # Phone numbers
        ]

        for pattern in prohibited_patterns:
            if re.search(pattern, description):
                raise ValidationError("Description contains prohibited content")

        return description

    @classmethod
    def validate_timeout_hours(cls, hours: Union[int, str]) -> int:
        """Validate delivery timeout hours"""
        try:
            hours = int(hours)
        except (ValueError, TypeError):
            raise ValidationError("Invalid timeout format")

        valid_timeouts = [
            24,
            48,
            72,
            120,
            168,
            336,
        ]  # 1 day, 2 days, 3 days, 5 days, 1 week, 2 weeks

        if hours not in valid_timeouts:
            raise ValidationError(f"Invalid timeout. Must be one of: {valid_timeouts}")

        return hours

    @classmethod
    def validate_user_id(cls, user_id: Union[int, str]) -> int:
        """Validate Telegram user ID"""
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            raise ValidationError("Invalid user ID format")

        if user_id <= 0:
            raise ValidationError("User ID must be positive")

        # Telegram user IDs are typically 9-10 digits
        if user_id < 100000000 or user_id > 9999999999:
            raise ValidationError("Invalid Telegram user ID range")

        return user_id

    @classmethod
    def sanitize_filename(cls, filename: str) -> str:
        """Sanitize filename for safe storage"""
        if not filename:
            return "unnamed_file"

        # Remove/replace dangerous characters
        filename = re.sub(r"[^\w\-_\.]", "_", filename)

        # Limit length
        if len(filename) > 100:
            name, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
            filename = name[:95] + ("." + ext if ext else "")

        return filename

    @classmethod
    def validate_network_selection(cls, network: str, currency: str) -> str:
        """Validate network selection for given currency"""
        valid_networks = {
            "USDT": ["ERC20", "TRC20"],
            "ETH": ["ETH"],
            "BTC": ["BTC"],
            "TRX": ["TRX"],
            "LTC": ["LTC"],
            "DOGE": ["DOGE"],
            "BCH": ["BCH"],
            "BNB": ["BSC"],
        }

        currency_networks = valid_networks.get(currency.upper(), [])

        if not currency_networks:
            raise ValidationError(f"Unsupported currency: {currency}")

        if network.upper() not in currency_networks:
            raise ValidationError(
                f"Invalid network {network} for {currency}. Valid networks: {currency_networks}"
            )

        return network.upper()

    @classmethod
    def sanitize_text_input(cls, text: str) -> str:
        """SECURITY: Comprehensive text sanitization to prevent injection attacks"""
        if not text:
            return text

        # Remove null bytes and control characters
        text = "".join(char for char in text if ord(char) > 31 or char in "\t\n\r")

        # Prevent SQL injection patterns
        dangerous_patterns = [
            r"(\bDROP\b|\bDELETE\b|\bUPDATE\b|\bINSERT\b|\bCREATE\b|\bALTER\b)",
            r"(--|#|/\*|\*/)",  # SQL comments
            r"(\bUNION\b|\bSELECT\b)",  # SQL injection
            r"(\bEXEC\b|\bEVAL\b|\bSYSTEM\b)",  # Command injection
            r"(<script|<iframe|<object|<embed)",  # XSS prevention
            r"(javascript:|vbscript:|data:)",  # Protocol injection
        ]

        for pattern in dangerous_patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)

        return text.strip()

    @classmethod
    def validate_and_sanitize_amount_input(
        cls, amount_text: str, min_amount: float = 0.01, max_amount: float = 100000.0
    ) -> Decimal:
        """SECURITY: Enhanced amount validation with injection protection"""
        if not amount_text:
            raise ValidationError("Amount cannot be empty")

        # SECURITY: Sanitize input first
        amount_text = cls.sanitize_text_input(amount_text)

        # Remove common formatting but preserve decimal structure
        amount_text = (
            amount_text.strip().replace(",", "").replace("$", "").replace(" ", "")
        )

        # SECURITY: Strict regex validation for numeric input only
        if not re.match(r"^\d+(\.\d{1,8})?$", amount_text):
            raise ValidationError(
                "Invalid amount format. Numbers and decimal point only."
            )

        try:
            amount = Decimal(amount_text)
        except InvalidOperation:
            raise ValidationError("Invalid amount format. Please enter a valid number")

        # Range validation
        if amount < Decimal(str(min_amount)):
            raise ValidationError(f"Amount must be at least ${min_amount}")

        if amount > Decimal(str(max_amount)):
            raise ValidationError(f"Amount cannot exceed ${max_amount:,.0f}")

        return amount

    @classmethod
    def validate_safe_user_input(
        cls, user_input: str, max_length: int = 100, allow_special_chars: bool = False
    ) -> str:
        """SECURITY: Validate and sanitize general user input"""
        if not user_input:
            raise ValidationError("Input cannot be empty")

        # SECURITY: Comprehensive sanitization
        user_input = cls.sanitize_text_input(user_input)

        if len(user_input.strip()) == 0:
            raise ValidationError("Input cannot be empty after sanitization")

        if len(user_input) > max_length:
            raise ValidationError(f"Input cannot exceed {max_length} characters")

        # Additional restrictions for special characters if not allowed
        if not allow_special_chars:
            if re.search(r'[<>"\'\\/{}\[\]();}&|`]', user_input):
                raise ValidationError("Input contains invalid characters")

        return user_input.strip()


# Convenience functions
def validate_and_clean_username(username: str) -> str:
    """Quick username validation"""
    return InputValidator.validate_username(username)


def validate_and_convert_amount(amount_str: str, min_amount: float = 5.0) -> Decimal:
    """Quick amount validation with minimum"""
    return InputValidator.validate_amount(amount_str, min_amount)


def is_valid_crypto_address(address: str, currency: str) -> bool:
    """Check if crypto address is valid without raising exception"""
    try:
        InputValidator.validate_crypto_address(address, currency)
        return True
    except ValidationError:
        return False


def is_valid_email(email: str) -> bool:
    """Check if email is valid without raising exception"""
    try:
        InputValidator.validate_email(email)
        return True
    except ValidationError:
        return False


def is_valid_phone(phone: str) -> bool:
    """Check if phone is valid without raising exception"""
    try:
        InputValidator.validate_phone(phone)
        return True
    except ValidationError:
        return False


# SECURITY: Enhanced validation functions for P2-1 Input Validation
def sanitize_text_input(text: str) -> str:
    """SECURITY: Comprehensive text sanitization to prevent injection attacks"""
    return InputValidator.sanitize_text_input(text)


def validate_and_sanitize_amount_input(
    amount_text: str, min_amount: float = 0.01, max_amount: float = 100000.0
) -> Decimal:
    """SECURITY: Enhanced amount validation with injection protection"""
    return InputValidator.validate_and_sanitize_amount_input(
        amount_text, min_amount, max_amount
    )


def validate_safe_user_input(
    user_input: str, max_length: int = 100, allow_special_chars: bool = False
) -> str:
    """SECURITY: Validate and sanitize general user input"""
    return InputValidator.validate_safe_user_input(
        user_input, max_length, allow_special_chars
    )
