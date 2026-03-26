"""
Precision Money Utility Module
===============================

CRITICAL: All monetary calculations MUST use Decimal, never float.

STORAGE STRATEGY:
- Database: DECIMAL(precision, scale) or INTEGER (smallest unit)
- Calculations: Python Decimal type
- Display: String formatting

ROUNDING: Always use ROUND_HALF_UP for financial calculations

USAGE EXAMPLES:
```python
# Converting user input
amount = string_to_decimal("10.50")  # Decimal('10.50')

# Safe calculations
total = safe_add(Decimal('10.10'), Decimal('5.05'))  # Decimal('15.15')
fee = calculate_percentage(Decimal('100'), Decimal('5'))  # Decimal('5.00')

# Integer storage (cents)
cents = decimal_to_cents(Decimal('10.50'))  # 1050
back = cents_to_decimal(cents)  # Decimal('10.50')

# Display
formatted = format_money(Decimal('1234.56'), 'USD')  # "$1,234.56"
```

NEVER DO:
- balance = 10.50  # WRONG - float
- result = amount * 1.05  # WRONG - float multiplication
- if balance == 10.50:  # WRONG - float comparison

ALWAYS DO:
- balance = Decimal('10.50')  # CORRECT
- result = safe_multiply(amount, Decimal('1.05'))  # CORRECT
- if balance == Decimal('10.50'):  # CORRECT

TEST EXAMPLES:
```python
# Example precision test cases
assert decimal_to_cents(Decimal('10.10')) == 1010
assert cents_to_decimal(1010) == Decimal('10.10')
assert safe_add(Decimal('10.10'), Decimal('0.05')) == Decimal('10.15')
assert safe_multiply(Decimal('10'), Decimal('1.05'), 2) == Decimal('10.50')
```
"""

from decimal import Decimal, ROUND_HALF_UP, getcontext, InvalidOperation
import logging

logger = logging.getLogger(__name__)

# Set global decimal precision for financial calculations
getcontext().prec = 28  # High precision for financial calculations

# Currency precision constants (decimal places)
USD_PRECISION = 2  # cents
NGN_PRECISION = 2  # kobo
BTC_PRECISION = 8  # satoshis
ETH_PRECISION = 18  # wei
USDT_PRECISION = 6  # micro USDT

# Precision multipliers for integer conversion
_PRECISION_MULTIPLIERS = {
    'USD': 10 ** USD_PRECISION,
    'NGN': 10 ** NGN_PRECISION,
    'BTC': 10 ** BTC_PRECISION,
    'XXBT': 10 ** BTC_PRECISION,  # Kraken BTC symbol
    'ETH': 10 ** ETH_PRECISION,
    'XETH': 10 ** ETH_PRECISION,  # Kraken ETH symbol
    'USDT': 10 ** USDT_PRECISION,
    'XUSDT': 10 ** USDT_PRECISION,
    'USDT-ERC20': 10 ** USDT_PRECISION,
    'USDT-TRC20': 10 ** USDT_PRECISION,
}

# Currency precision mapping
_CURRENCY_PRECISION = {
    'USD': USD_PRECISION,
    'NGN': NGN_PRECISION,
    'BTC': BTC_PRECISION,
    'XXBT': BTC_PRECISION,
    'ETH': ETH_PRECISION,
    'XETH': ETH_PRECISION,
    'USDT': USDT_PRECISION,
    'XUSDT': USDT_PRECISION,
    'USDT-ERC20': USDT_PRECISION,
    'USDT-TRC20': USDT_PRECISION,
    'LTC': 8,
    'XLTC': 8,
    'BCH': 8,
    'XBCH': 8,
    'DOGE': 8,
    'XXDG': 8,
    'TRX': 6,
    'XTRX': 6,
    'BSC': 8,
}


# ============================================================================
# INTEGER CONVERSION FUNCTIONS
# ============================================================================

def decimal_to_cents(amount: Decimal) -> int:
    """
    Convert USD/NGN Decimal to cents/kobo (integer).
    
    Args:
        amount: Decimal amount in USD or NGN
        
    Returns:
        Integer representation in cents/kobo
        
    Example:
        >>> decimal_to_cents(Decimal('10.50'))
        1050
        >>> decimal_to_cents(Decimal('0.01'))
        1
    """
    if not isinstance(amount, Decimal):
        logger.warning(f"⚠️ decimal_to_cents received non-Decimal: {type(amount)}")
        amount = Decimal(str(amount))
    
    # Multiply then round to nearest integer using ROUND_HALF_UP
    cents_decimal = amount * Decimal('100')
    cents_rounded = cents_decimal.to_integral_value(rounding=ROUND_HALF_UP)
    return int(cents_rounded)


def cents_to_decimal(cents: int) -> Decimal:
    """
    Convert cents/kobo to Decimal USD/NGN.
    
    Args:
        cents: Integer cents/kobo amount
        
    Returns:
        Decimal representation in USD/NGN
        
    Example:
        >>> cents_to_decimal(1050)
        Decimal('10.50')
        >>> cents_to_decimal(1)
        Decimal('0.01')
    """
    if not isinstance(cents, int):
        logger.warning(f"⚠️ cents_to_decimal received non-int: {type(cents)}")
        cents = int(cents)
    
    amount = Decimal(cents) / Decimal('100')
    return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def decimal_to_satoshis(amount: Decimal) -> int:
    """
    Convert BTC Decimal to satoshis (integer).
    
    Args:
        amount: Decimal amount in BTC
        
    Returns:
        Integer representation in satoshis
        
    Example:
        >>> decimal_to_satoshis(Decimal('0.00000001'))
        1
        >>> decimal_to_satoshis(Decimal('1.0'))
        100000000
    """
    if not isinstance(amount, Decimal):
        logger.warning(f"⚠️ decimal_to_satoshis received non-Decimal: {type(amount)}")
        amount = Decimal(str(amount))
    
    # Multiply then round to nearest integer using ROUND_HALF_UP
    satoshis_decimal = amount * Decimal('100000000')
    satoshis_rounded = satoshis_decimal.to_integral_value(rounding=ROUND_HALF_UP)
    return int(satoshis_rounded)


def satoshis_to_decimal(satoshis: int) -> Decimal:
    """
    Convert satoshis to Decimal BTC.
    
    Args:
        satoshis: Integer satoshi amount
        
    Returns:
        Decimal representation in BTC
        
    Example:
        >>> satoshis_to_decimal(1)
        Decimal('0.00000001')
        >>> satoshis_to_decimal(100000000)
        Decimal('1.00000000')
    """
    if not isinstance(satoshis, int):
        logger.warning(f"⚠️ satoshis_to_decimal received non-int: {type(satoshis)}")
        satoshis = int(satoshis)
    
    amount = Decimal(satoshis) / Decimal('100000000')
    return amount.quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP)


def decimal_to_wei(amount: Decimal) -> int:
    """
    Convert ETH Decimal to wei (integer).
    
    Args:
        amount: Decimal amount in ETH
        
    Returns:
        Integer representation in wei
        
    Example:
        >>> decimal_to_wei(Decimal('1.0'))
        1000000000000000000
        >>> decimal_to_wei(Decimal('0.000000000000000001'))
        1
    """
    if not isinstance(amount, Decimal):
        logger.warning(f"⚠️ decimal_to_wei received non-Decimal: {type(amount)}")
        amount = Decimal(str(amount))
    
    # Multiply then round to nearest integer using ROUND_HALF_UP
    wei_decimal = amount * Decimal('1000000000000000000')
    wei_rounded = wei_decimal.to_integral_value(rounding=ROUND_HALF_UP)
    return int(wei_rounded)


def wei_to_decimal(wei: int) -> Decimal:
    """
    Convert wei to Decimal ETH.
    
    Args:
        wei: Integer wei amount
        
    Returns:
        Decimal representation in ETH
        
    Example:
        >>> wei_to_decimal(1)
        Decimal('0.000000000000000001')
        >>> wei_to_decimal(1000000000000000000)
        Decimal('1.000000000000000000')
    """
    if not isinstance(wei, int):
        logger.warning(f"⚠️ wei_to_decimal received non-int: {type(wei)}")
        wei = int(wei)
    
    amount = Decimal(wei) / Decimal('1000000000000000000')
    return amount.quantize(Decimal('0.000000000000000001'), rounding=ROUND_HALF_UP)


def decimal_to_smallest_unit(amount: Decimal, currency: str) -> int:
    """
    Generic converter based on currency to smallest unit.
    
    Args:
        amount: Decimal amount
        currency: Currency code (BTC, ETH, USDT, USD, NGN, etc.)
        
    Returns:
        Integer representation in smallest unit
        
    Example:
        >>> decimal_to_smallest_unit(Decimal('10.50'), 'USD')
        1050
        >>> decimal_to_smallest_unit(Decimal('1.0'), 'BTC')
        100000000
        >>> decimal_to_smallest_unit(Decimal('1.0'), 'ETH')
        1000000000000000000
    """
    if not isinstance(amount, Decimal):
        logger.warning(f"⚠️ decimal_to_smallest_unit received non-Decimal: {type(amount)}")
        amount = Decimal(str(amount))
    
    currency_upper = currency.upper()
    
    # Get multiplier for currency
    multiplier = _PRECISION_MULTIPLIERS.get(currency_upper)
    
    if multiplier is None:
        logger.error(f"❌ Unknown currency for conversion: {currency}")
        raise ValueError(f"Unsupported currency: {currency}")
    
    # Multiply then round to nearest integer using ROUND_HALF_UP
    smallest_unit_decimal = amount * Decimal(multiplier)
    smallest_unit_rounded = smallest_unit_decimal.to_integral_value(rounding=ROUND_HALF_UP)
    return int(smallest_unit_rounded)


def smallest_unit_to_decimal(units: int, currency: str) -> Decimal:
    """
    Generic converter from smallest unit to Decimal.
    
    Args:
        units: Integer amount in smallest unit
        currency: Currency code (BTC, ETH, USDT, USD, NGN, etc.)
        
    Returns:
        Decimal representation
        
    Example:
        >>> smallest_unit_to_decimal(1050, 'USD')
        Decimal('10.50')
        >>> smallest_unit_to_decimal(100000000, 'BTC')
        Decimal('1.00000000')
        >>> smallest_unit_to_decimal(1000000000000000000, 'ETH')
        Decimal('1.000000000000000000')
    """
    if not isinstance(units, int):
        logger.warning(f"⚠️ smallest_unit_to_decimal received non-int: {type(units)}")
        units = int(units)
    
    currency_upper = currency.upper()
    
    # Get multiplier and precision for currency
    multiplier = _PRECISION_MULTIPLIERS.get(currency_upper)
    precision = _CURRENCY_PRECISION.get(currency_upper)
    
    if multiplier is None or precision is None:
        logger.error(f"❌ Unknown currency for conversion: {currency}")
        raise ValueError(f"Unsupported currency: {currency}")
    
    amount = Decimal(units) / Decimal(multiplier)
    
    # Create quantize format string based on precision
    quantize_format = Decimal('0.1') ** precision
    
    return amount.quantize(quantize_format, rounding=ROUND_HALF_UP)


# ============================================================================
# SAFE ARITHMETIC OPERATIONS
# ============================================================================

def safe_add(*amounts: Decimal) -> Decimal:
    """
    Safely add multiple Decimal amounts with ROUND_HALF_UP.
    
    Args:
        *amounts: Variable number of Decimal amounts to add
        
    Returns:
        Sum of all amounts, rounded to 2 decimal places
        
    Example:
        >>> safe_add(Decimal('10.10'), Decimal('5.05'))
        Decimal('15.15')
        >>> safe_add(Decimal('1.111'), Decimal('2.222'), Decimal('3.333'))
        Decimal('6.67')
    """
    total = Decimal('0')
    
    for amount in amounts:
        if not isinstance(amount, Decimal):
            logger.warning(f"⚠️ safe_add received non-Decimal: {type(amount)}")
            amount = Decimal(str(amount))
        
        total += amount
    
    return total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def safe_subtract(minuend: Decimal, subtrahend: Decimal) -> Decimal:
    """
    Safely subtract Decimals with ROUND_HALF_UP.
    
    Args:
        minuend: Amount to subtract from
        subtrahend: Amount to subtract
        
    Returns:
        Difference, rounded to 2 decimal places
        
    Example:
        >>> safe_subtract(Decimal('10.50'), Decimal('5.25'))
        Decimal('5.25')
        >>> safe_subtract(Decimal('10.111'), Decimal('5.222'))
        Decimal('4.89')
    """
    if not isinstance(minuend, Decimal):
        logger.warning(f"⚠️ safe_subtract minuend non-Decimal: {type(minuend)}")
        minuend = Decimal(str(minuend))
    
    if not isinstance(subtrahend, Decimal):
        logger.warning(f"⚠️ safe_subtract subtrahend non-Decimal: {type(subtrahend)}")
        subtrahend = Decimal(str(subtrahend))
    
    result = minuend - subtrahend
    return result.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def safe_multiply(amount: Decimal, multiplier: Decimal, precision: int = 2) -> Decimal:
    """
    Safely multiply with controlled rounding.
    
    Args:
        amount: Base amount
        multiplier: Multiplication factor
        precision: Decimal places for result (default: 2)
        
    Returns:
        Product, rounded to specified precision
        
    Example:
        >>> safe_multiply(Decimal('10'), Decimal('1.05'), 2)
        Decimal('10.50')
        >>> safe_multiply(Decimal('100'), Decimal('0.05'), 2)
        Decimal('5.00')
        >>> safe_multiply(Decimal('1'), Decimal('0.12345678'), 8)
        Decimal('0.12345678')
    """
    if not isinstance(amount, Decimal):
        logger.warning(f"⚠️ safe_multiply amount non-Decimal: {type(amount)}")
        amount = Decimal(str(amount))
    
    if not isinstance(multiplier, Decimal):
        logger.warning(f"⚠️ safe_multiply multiplier non-Decimal: {type(multiplier)}")
        multiplier = Decimal(str(multiplier))
    
    result = amount * multiplier
    
    # Create quantize format based on precision
    quantize_format = Decimal('0.1') ** precision
    
    return result.quantize(quantize_format, rounding=ROUND_HALF_UP)


def safe_divide(dividend: Decimal, divisor: Decimal, precision: int = 2) -> Decimal:
    """
    Safely divide with controlled rounding and zero-check.
    
    Args:
        dividend: Amount to divide
        divisor: Amount to divide by
        precision: Decimal places for result (default: 2)
        
    Returns:
        Quotient, rounded to specified precision
        
    Raises:
        ValueError: If divisor is zero
        
    Example:
        >>> safe_divide(Decimal('10'), Decimal('2'), 2)
        Decimal('5.00')
        >>> safe_divide(Decimal('100'), Decimal('3'), 2)
        Decimal('33.33')
        >>> safe_divide(Decimal('1'), Decimal('3'), 8)
        Decimal('0.33333333')
    """
    if not isinstance(dividend, Decimal):
        logger.warning(f"⚠️ safe_divide dividend non-Decimal: {type(dividend)}")
        dividend = Decimal(str(dividend))
    
    if not isinstance(divisor, Decimal):
        logger.warning(f"⚠️ safe_divide divisor non-Decimal: {type(divisor)}")
        divisor = Decimal(str(divisor))
    
    if divisor == Decimal('0'):
        logger.error("❌ Division by zero attempted")
        raise ValueError("Cannot divide by zero")
    
    result = dividend / divisor
    
    # Create quantize format based on precision
    quantize_format = Decimal('0.1') ** precision
    
    return result.quantize(quantize_format, rounding=ROUND_HALF_UP)


def round_money(amount: Decimal, precision: int = 2) -> Decimal:
    """
    Round to specified precision using ROUND_HALF_UP.
    
    Args:
        amount: Decimal amount to round
        precision: Decimal places (default: 2)
        
    Returns:
        Rounded amount
        
    Example:
        >>> round_money(Decimal('10.125'), 2)
        Decimal('10.13')
        >>> round_money(Decimal('10.124'), 2)
        Decimal('10.12')
        >>> round_money(Decimal('1.23456789'), 8)
        Decimal('1.23456789')
    """
    if not isinstance(amount, Decimal):
        logger.warning(f"⚠️ round_money received non-Decimal: {type(amount)}")
        amount = Decimal(str(amount))
    
    # Create quantize format based on precision
    quantize_format = Decimal('0.1') ** precision
    
    return amount.quantize(quantize_format, rounding=ROUND_HALF_UP)


# ============================================================================
# STRING CONVERSION (for input/display)
# ============================================================================

def string_to_decimal(amount_str: str) -> Decimal:
    """
    Convert user input string to Decimal safely.
    
    Args:
        amount_str: String representation of amount
        
    Returns:
        Decimal representation, or Decimal('0') if invalid
        
    Example:
        >>> string_to_decimal("10.50")
        Decimal('10.50')
        >>> string_to_decimal("1,234.56")
        Decimal('1234.56')
        >>> string_to_decimal("")
        Decimal('0')
        >>> string_to_decimal("invalid")
        Decimal('0')
    """
    if not amount_str or not isinstance(amount_str, str):
        logger.warning(f"⚠️ string_to_decimal received invalid input: {amount_str}")
        return Decimal('0')
    
    # Clean the string - remove commas and whitespace
    cleaned = amount_str.strip().replace(',', '').replace(' ', '')
    
    if not cleaned:
        return Decimal('0')
    
    try:
        amount = Decimal(cleaned)
        
        # Validate it's not negative (unless explicitly allowed)
        if amount < Decimal('0'):
            logger.warning(f"⚠️ Negative amount detected: {amount}")
        
        return amount
        
    except (InvalidOperation, ValueError) as e:
        logger.error(f"❌ Failed to convert string to Decimal: '{amount_str}' - {e}")
        return Decimal('0')


def decimal_to_string(amount: Decimal, precision: int = 2) -> str:
    """
    Convert Decimal to formatted string for display.
    
    Args:
        amount: Decimal amount
        precision: Decimal places to display (default: 2)
        
    Returns:
        Formatted string (e.g., "10.50" not "10.5")
        
    Example:
        >>> decimal_to_string(Decimal('10.5'), 2)
        '10.50'
        >>> decimal_to_string(Decimal('1234.567'), 2)
        '1234.57'
        >>> decimal_to_string(Decimal('0.12345678'), 8)
        '0.12345678'
    """
    if not isinstance(amount, Decimal):
        logger.warning(f"⚠️ decimal_to_string received non-Decimal: {type(amount)}")
        amount = Decimal(str(amount))
    
    # Create quantize format based on precision
    quantize_format = Decimal('0.1') ** precision
    rounded = amount.quantize(quantize_format, rounding=ROUND_HALF_UP)
    
    # Format with fixed precision
    format_string = f"{{:.{precision}f}}"
    return format_string.format(rounded)


def format_money(amount: Decimal, currency: str) -> str:
    """
    Format Decimal as money with currency symbol.
    
    Args:
        amount: Decimal amount
        currency: Currency code (USD, NGN, BTC, ETH, etc.)
        
    Returns:
        Formatted money string with symbol and thousands separators
        
    Example:
        >>> format_money(Decimal('1234.56'), 'USD')
        '$1,234.56'
        >>> format_money(Decimal('1234.56'), 'NGN')
        '₦1,234.56'
        >>> format_money(Decimal('0.00012300'), 'BTC')
        '0.00012300 BTC'
        >>> format_money(Decimal('1234.567890'), 'ETH')
        '1234.567890000000000000 ETH'
    """
    if not isinstance(amount, Decimal):
        logger.warning(f"⚠️ format_money received non-Decimal: {type(amount)}")
        amount = Decimal(str(amount))
    
    currency_upper = currency.upper()
    
    # Get precision for currency
    precision = get_currency_precision(currency_upper)
    
    # Round to appropriate precision
    rounded = normalize_for_currency(amount, currency_upper)
    
    # Currency symbols and formatting
    if currency_upper in ['USD', 'USDT', 'XUSDT', 'USDT-ERC20', 'USDT-TRC20']:
        symbol = '$'
        return f"{symbol}{rounded:,.{precision}f}"
    elif currency_upper == 'NGN':
        symbol = '₦'
        return f"{symbol}{rounded:,.{precision}f}"
    elif currency_upper in ['BTC', 'XXBT', 'ETH', 'XETH', 'LTC', 'XLTC', 'BCH', 'XBCH', 'DOGE', 'XXDG', 'TRX', 'XTRX', 'BSC']:
        # For crypto, show full precision without thousands separator
        format_string = f"{{:.{precision}f}}"
        return f"{format_string.format(rounded)} {currency_upper}"
    else:
        # Generic format
        return f"{rounded:,.{precision}f} {currency_upper}"


# ============================================================================
# FLOAT MIGRATION HELPER
# ============================================================================

def float_to_decimal(value: float) -> Decimal:
    """
    Convert float to Decimal (MIGRATION ONLY - log warning).
    
    WARNING: This function should only be used during migration from float-based
    code to Decimal-based code. New code should never use floats for money.
    
    Args:
        value: Float value to convert
        
    Returns:
        Decimal representation
        
    Example:
        >>> float_to_decimal(10.50)  # Will log warning
        Decimal('10.5')
    """
    logger.warning(f"⚠️ FLOAT_DETECTED: Converting float {value} to Decimal - update code to use Decimal")
    
    # Convert via string to avoid float precision issues
    # This is the safest way to convert float to Decimal
    return Decimal(str(value))


# ============================================================================
# VALIDATION FUNCTIONS
# ============================================================================

def is_valid_money_amount(amount: Decimal, min_amount: Decimal = Decimal('0')) -> bool:
    """
    Validate amount is positive and reasonable.
    
    Args:
        amount: Decimal amount to validate
        min_amount: Minimum allowed amount (default: 0)
        
    Returns:
        True if valid, False otherwise
        
    Example:
        >>> is_valid_money_amount(Decimal('10.50'))
        True
        >>> is_valid_money_amount(Decimal('-5'))
        False
        >>> is_valid_money_amount(Decimal('5'), Decimal('10'))
        False
        >>> is_valid_money_amount(Decimal('999999999999'))
        True
    """
    if not isinstance(amount, Decimal):
        logger.warning(f"⚠️ is_valid_money_amount received non-Decimal: {type(amount)}")
        return False
    
    # Check if amount is at least min_amount
    if amount < min_amount:
        logger.debug(f"Amount {amount} is below minimum {min_amount}")
        return False
    
    # Check for reasonable upper bound (999 billion)
    if amount > Decimal('999999999999'):
        logger.warning(f"Amount {amount} exceeds reasonable maximum")
        return False
    
    return True


def is_sufficient_balance(balance: Decimal, required: Decimal) -> bool:
    """
    Check if balance >= required (safe comparison).
    
    Args:
        balance: Current balance
        required: Required amount
        
    Returns:
        True if balance is sufficient, False otherwise
        
    Example:
        >>> is_sufficient_balance(Decimal('100'), Decimal('50'))
        True
        >>> is_sufficient_balance(Decimal('100'), Decimal('100'))
        True
        >>> is_sufficient_balance(Decimal('100'), Decimal('100.01'))
        False
    """
    if not isinstance(balance, Decimal):
        logger.warning(f"⚠️ is_sufficient_balance balance non-Decimal: {type(balance)}")
        balance = Decimal(str(balance))
    
    if not isinstance(required, Decimal):
        logger.warning(f"⚠️ is_sufficient_balance required non-Decimal: {type(required)}")
        required = Decimal(str(required))
    
    return balance >= required


def calculate_percentage(amount: Decimal, percentage: Decimal) -> Decimal:
    """
    Calculate percentage of amount (e.g., 5% fee).
    
    Args:
        amount: Base amount
        percentage: Percentage (e.g., Decimal('5') for 5%)
        
    Returns:
        Percentage of amount, rounded to 2 decimal places
        
    Example:
        >>> calculate_percentage(Decimal('100'), Decimal('5'))
        Decimal('5.00')
        >>> calculate_percentage(Decimal('200'), Decimal('2.5'))
        Decimal('5.00')
        >>> calculate_percentage(Decimal('1000'), Decimal('0.5'))
        Decimal('5.00')
    """
    if not isinstance(amount, Decimal):
        logger.warning(f"⚠️ calculate_percentage amount non-Decimal: {type(amount)}")
        amount = Decimal(str(amount))
    
    if not isinstance(percentage, Decimal):
        logger.warning(f"⚠️ calculate_percentage percentage non-Decimal: {type(percentage)}")
        percentage = Decimal(str(percentage))
    
    # Calculate percentage (percentage / 100 * amount)
    result = (percentage / Decimal('100')) * amount
    
    return result.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


# ============================================================================
# CURRENCY-SPECIFIC HELPERS
# ============================================================================

def get_currency_precision(currency: str) -> int:
    """
    Get decimal precision for currency (USD: 2, BTC: 8, etc.).
    
    Args:
        currency: Currency code
        
    Returns:
        Number of decimal places for currency
        
    Example:
        >>> get_currency_precision('USD')
        2
        >>> get_currency_precision('BTC')
        8
        >>> get_currency_precision('ETH')
        18
        >>> get_currency_precision('USDT')
        6
    """
    currency_upper = currency.upper()
    
    precision = _CURRENCY_PRECISION.get(currency_upper)
    
    if precision is None:
        logger.warning(f"⚠️ Unknown currency precision for: {currency}, defaulting to 2")
        return 2  # Default to 2 decimal places for unknown currencies
    
    return precision


def normalize_for_currency(amount: Decimal, currency: str) -> Decimal:
    """
    Round to appropriate precision for currency.
    
    Args:
        amount: Decimal amount
        currency: Currency code
        
    Returns:
        Amount rounded to currency's precision
        
    Example:
        >>> normalize_for_currency(Decimal('10.12345'), 'USD')
        Decimal('10.12')
        >>> normalize_for_currency(Decimal('0.123456789'), 'BTC')
        Decimal('0.12345679')
        >>> normalize_for_currency(Decimal('1.123'), 'NGN')
        Decimal('1.12')
    """
    if not isinstance(amount, Decimal):
        logger.warning(f"⚠️ normalize_for_currency received non-Decimal: {type(amount)}")
        amount = Decimal(str(amount))
    
    precision = get_currency_precision(currency)
    
    # Create quantize format based on precision
    quantize_format = Decimal('0.1') ** precision
    
    return amount.quantize(quantize_format, rounding=ROUND_HALF_UP)
