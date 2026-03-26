"""Mobile-optimized QR code generation service with dynamic branding"""

import qrcode
import qrcode.constants
from qrcode.main import QRCode
from io import BytesIO
import base64
import logging
import re
from typing import Optional
from decimal import Decimal
from config import Config

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

logger = logging.getLogger(__name__)


class QRCodeService:
    """Service for generating mobile-optimized QR codes with dynamic branding and enhanced error correction"""
    
    # Dynamic branding constants
    @property
    def BRAND_NAME(self):
        return Config.PLATFORM_NAME
    
    BRAND_TAGLINE = "Secure Crypto Escrow"
    WATERMARK_COLOR = "#2E3440"  # Dark gray for professional look
    WATERMARK_BG_COLOR = "#ECEFF4"  # Light gray background

    @classmethod
    def validate_crypto_address(
        cls, address: str, currency: Optional[str] = None
    ) -> bool:
        """Basic validation for cryptocurrency addresses"""
        if not address or len(address.strip()) == 0:
            return False

        address = address.strip()

        # Basic length and character checks
        if currency:
            currency_upper = currency.upper()
            if currency_upper == "BTC":
                # Bitcoin addresses: 26-35 characters, alphanumeric
                return 26 <= len(address) <= 35 and bool(
                    re.match(
                        r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$|^bc1[a-z0-9]{39,59}$",
                        address,
                    )
                )
            elif currency_upper in ["ETH", "USDT-ERC20", "USDC-ERC20"]:
                # Ethereum addresses: 42 characters, starts with 0x
                return len(address) == 42 and bool(
                    re.match(r"^0x[a-fA-F0-9]{40}$", address)
                )
            elif currency_upper in ["USDT-TRC20"]:
                # Tron addresses: 34 characters, starts with T
                return len(address) == 34 and bool(
                    re.match(r"^T[a-zA-Z0-9]{33}$", address)
                )
            elif currency_upper == "LTC":
                # Litecoin addresses: Legacy (L, M, 3) or Bech32 (ltc1)
                return bool(
                    re.match(r"^[LM3][a-km-zA-HJ-NP-Z1-9]{25,34}$", address) or
                    re.match(r"^ltc1[a-z0-9]{39,59}$", address)
                )
            elif currency_upper == "DOGE":
                # Dogecoin addresses: Legacy (D) or multisig (9, A)
                return bool(
                    re.match(r"^[D9A][a-km-zA-HJ-NP-Z1-9]{25,34}$", address)
                )
            elif currency_upper == "BCH":
                # Bitcoin Cash addresses: Legacy or CashAddr format
                return bool(
                    re.match(r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$", address) or
                    re.match(r"^bitcoincash:[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{42,62}$", address) or
                    re.match(r"^[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{42,62}$", address)
                )
            elif currency_upper in ["BSC", "BNB"]:
                # Binance Smart Chain addresses: Same format as Ethereum
                return len(address) == 42 and bool(
                    re.match(r"^0x[a-fA-F0-9]{40}$", address)
                )
            elif currency_upper == "TRX":
                # Tron addresses: 34 characters, starts with T
                return len(address) == 34 and bool(
                    re.match(r"^T[a-zA-Z0-9]{33}$", address)
                )

        # Generic validation: reasonable length and no whitespace
        return 20 <= len(address) <= 100 and not any(c.isspace() for c in address)
    
    @classmethod
    def _add_lockbay_watermark(cls, qr_image, include_tagline: bool = True) -> Image:
        """
        Add platform branding watermark below QR code
        
        Args:
            qr_image: PIL Image object of the QR code
            include_tagline: Whether to include the tagline below brand name
            
        Returns:
            PIL Image with platform branding added below QR code
        """
        if not PIL_AVAILABLE:
            logger.warning("PIL not available, returning original QR image without watermark")
            return qr_image
            
        try:
            # Calculate dimensions for watermarked image
            qr_width, qr_height = qr_image.size
            
            # Calculate watermark dimensions
            # Add space for brand name and optionally tagline
            watermark_height = 60 if include_tagline else 40
            padding = 20
            
            # Create new image with space for watermark
            total_height = qr_height + watermark_height + padding
            watermark_image = Image.new('RGB', (qr_width, total_height), 'white')
            
            # Paste QR code at the top
            watermark_image.paste(qr_image, (0, 0))
            
            # Create drawing context
            draw = ImageDraw.Draw(watermark_image)
            
            # Try to use system fonts, fallback to default
            try:
                # Try common system fonts
                font_brand = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
                font_tagline = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
            except (OSError, IOError):
                try:
                    # Fallback to other common paths
                    font_brand = ImageFont.truetype("arial.ttf", 16) 
                    font_tagline = ImageFont.truetype("arial.ttf", 12)
                except (OSError, IOError):
                    # Use default font as final fallback
                    font_brand = ImageFont.load_default()
                    font_tagline = ImageFont.load_default()
            
            # Calculate text positions for centering (using compatible method)
            try:
                # Try newer PIL textbbox method - need to handle the bbox correctly
                try:
                    brand_bbox = draw.textbbox((0, 0), cls.BRAND_NAME, font=font_brand)
                    brand_width = brand_bbox[2] - brand_bbox[0]
                except TypeError:
                    # textbbox might need different parameters, try with anchor
                    brand_bbox = draw.textbbox((0, 0), cls.BRAND_NAME, font=font_brand, anchor="lt")
                    brand_width = brand_bbox[2] - brand_bbox[0]
            except (AttributeError, TypeError):
                # Fallback to textsize method for older PIL versions
                try:
                    brand_width, _ = draw.textsize(cls.BRAND_NAME, font=font_brand)
                except AttributeError:
                    # Final fallback: estimate based on text length
                    brand_width = len(cls.BRAND_NAME) * 10
            
            brand_x = (qr_width - brand_width) // 2
            brand_y = qr_height + 10
            
            # Draw brand name
            brand_name = Config.PLATFORM_NAME
            draw.text(
                (brand_x, brand_y), 
                brand_name, 
                font=font_brand, 
                fill=cls.WATERMARK_COLOR
            )
            
            # Draw tagline if requested
            if include_tagline:
                try:
                    # Try newer PIL textbbox method - need to handle the bbox correctly
                    try:
                        tagline_bbox = draw.textbbox((0, 0), cls.BRAND_TAGLINE, font=font_tagline)
                        tagline_width = tagline_bbox[2] - tagline_bbox[0]
                    except TypeError:
                        # textbbox might need different parameters, try with anchor
                        tagline_bbox = draw.textbbox((0, 0), cls.BRAND_TAGLINE, font=font_tagline, anchor="lt")
                        tagline_width = tagline_bbox[2] - tagline_bbox[0]
                except (AttributeError, TypeError):
                    # Fallback to textsize method for older PIL versions
                    try:
                        tagline_width, _ = draw.textsize(cls.BRAND_TAGLINE, font=font_tagline)
                    except AttributeError:
                        # Final fallback: estimate based on text length
                        tagline_width = len(cls.BRAND_TAGLINE) * 8
                
                tagline_x = (qr_width - tagline_width) // 2
                tagline_y = brand_y + 25
                
                draw.text(
                    (tagline_x, tagline_y), 
                    cls.BRAND_TAGLINE, 
                    font=font_tagline, 
                    fill=cls.WATERMARK_COLOR
                )
            
            logger.info(f"Successfully added {Config.PLATFORM_NAME} watermark to QR code")
            return watermark_image
            
        except Exception as e:
            logger.error(f"Failed to add {Config.PLATFORM_NAME} watermark: {e}")
            # Return original QR code if watermarking fails
            return qr_image

    @classmethod
    def generate_qr_code(
        cls, data: str, size: int = 12, border: int = 4, add_branding: bool = True
    ) -> Optional[str]:
        """Generate a mobile-optimized QR code with platform branding and return as base64 encoded image"""
        try:
            # Create QR code instance with mobile-optimized settings
            # Use medium error correction for crypto QR codes to ensure compatibility
            qr = QRCode(
                version=None,  # Auto-determine version based on data length
                error_correction=qrcode.constants.ERROR_CORRECT_M,  # Medium error correction for better compatibility
                box_size=size,  # Larger box size for better mobile readability
                border=border,
            )

            # Add data and optimize
            qr.add_data(data)
            qr.make(fit=True)

            # Create image with high contrast for mobile cameras
            img = qr.make_image(fill_color="black", back_color="white")

            # Ensure optimal size for mobile scanning (400x400 pixels for better readability)
            target_size = 400
            if img.size[0] < target_size:
                # Calculate scale factor to reach target size
                scale_factor = target_size / img.size[0]
                new_size = (
                    int(img.size[0] * scale_factor),
                    int(img.size[1] * scale_factor),
                )
                # Use high-quality resampling for better quality when upscaling
                try:
                    from PIL import Image
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                except (AttributeError, ImportError):
                    # Fallback to nearest neighbor if LANCZOS not available
                    img = img.resize(new_size, resample=0)

            # Add platform branding watermark if requested
            if add_branding:
                try:
                    img = cls._add_lockbay_watermark(img, include_tagline=True)
                    logger.info(f"{Config.PLATFORM_NAME} branding added to QR code")
                except Exception as e:
                    logger.warning(f"Failed to add branding to QR code, using original: {e}")
                    # Continue with original image if watermarking fails

            # Convert to base64
            buffered = BytesIO()
            img.save(buffered, format="PNG")
            img_base64 = base64.b64encode(buffered.getvalue()).decode()

            return img_base64

        except Exception as e:
            logger.error(f"QR code generation failed: {e}", exc_info=True)
            return None

    @classmethod
    def generate_deposit_qr(
        cls,
        address: str,
        amount: Optional[Decimal] = None,
        currency: Optional[str] = None,
    ) -> Optional[str]:
        """Generate mobile-optimized QR code for cryptocurrency deposit"""
        try:
            # Validate address format
            if not address or len(address.strip()) == 0:
                logger.error("Invalid address for QR generation")
                return None

            address = address.strip()

            # Validate address format if currency is specified
            if currency and not cls.validate_crypto_address(address, currency):
                logger.warning(
                    f"Address validation failed for {currency}: {address[:10]}..."
                )
                # Continue anyway - validation is informational, not blocking

            # Create properly formatted payment URI if amount is specified
            if amount and currency and amount > 0:
                currency_upper = currency.upper()

                if currency_upper == "BTC":
                    # Bitcoin BIP-21 URI scheme - remove trailing zeros for better compatibility
                    amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
                    qr_data = f"bitcoin:{address}?amount={amount_str}"
                elif currency_upper == "ETH":
                    # Ethereum EIP-681 URI scheme
                    qr_data = f"ethereum:{address}?value={int(amount * 10**18)}"  # Convert to wei
                elif currency_upper in ["USDT-ERC20", "USDC-ERC20"]:
                    # ERC-20 tokens on Ethereum
                    qr_data = (
                        f"ethereum:{address}"  # Fallback to address only for tokens
                    )
                elif currency_upper in ["USDT-TRC20"]:
                    # Tron network tokens
                    qr_data = f"tron:{address}"
                elif currency_upper == "LTC":
                    # Litecoin URI scheme - remove trailing zeros for better compatibility
                    amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
                    qr_data = f"litecoin:{address}?amount={amount_str}"
                elif currency_upper == "DOGE":
                    # Dogecoin URI scheme - remove trailing zeros for better compatibility
                    amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
                    qr_data = f"dogecoin:{address}?amount={amount_str}"
                elif currency_upper == "BCH":
                    # Bitcoin Cash URI scheme - remove trailing zeros for better compatibility
                    amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
                    qr_data = f"bitcoincash:{address}?amount={amount_str}"
                elif currency_upper in ["BSC", "BNB"]:
                    # Binance Smart Chain - use Ethereum format
                    qr_data = f"ethereum:{address}?value={int(amount * 10**18)}"  # Convert to wei
                elif currency_upper == "TRX":
                    # Tron network
                    qr_data = f"tron:{address}?amount={amount:.6f}"  # TRX uses 6 decimals
                else:
                    # Fallback to just address for unknown currencies
                    qr_data = address
            else:
                # No amount specified, just use address
                qr_data = address
            
            # Always prefer plain address for better mobile camera compatibility
            # Store URI data for potential future use, but prioritize plain address

            logger.info(
                f"Generating QR for: {qr_data[:50]}..."
            )  # Log first 50 chars for debugging
            
            # For mobile camera compatibility, use plain address format
            # Most phone cameras don't recognize crypto URI schemes
            logger.info("Generating camera-compatible QR with plain address for better recognition")
            
            # Try plain address first (better camera compatibility) with platform branding
            qr_result = cls.generate_qr_code(address, add_branding=True)
            
            # If plain address fails, try URI format as fallback for crypto wallets
            if not qr_result and amount and currency:
                logger.warning("Plain address QR failed, trying URI format as fallback")
                qr_result = cls.generate_qr_code(qr_data, add_branding=True)
                
            return qr_result

        except Exception as e:
            logger.error(f"Deposit QR generation failed: {e}")
            return None

    @classmethod
    def generate_ascii_qr(cls, data: str, size: str = "small") -> str:
        """Generate ASCII art QR code for text display"""
        try:
            # Determine size parameters
            if size == "small":
                box_size = 1
                border = 1
            elif size == "large":
                box_size = 2
                border = 2
            else:
                box_size = 1
                border = 1

            qr = QRCode(
                version=None,  # Auto-determine version
                error_correction=qrcode.constants.ERROR_CORRECT_M,  # Medium error correction for ASCII
                box_size=box_size,
                border=border,
            )

            qr.add_data(data)
            qr.make(fit=True)

            # Generate ASCII representation
            matrix = qr.get_matrix()
            ascii_qr = ""

            for row in matrix:
                line = ""
                for cell in row:
                    line += "██" if cell else "  "
                ascii_qr += line + "\n"

            return ascii_qr

        except Exception as e:
            logger.error(f"ASCII QR generation failed: {e}")
            return cls._fallback_ascii_qr()

    @classmethod
    def _fallback_ascii_qr(cls) -> str:
        """Fallback ASCII QR when generation fails"""
        return """
██████████████  ██████████████
██          ██  ██          ██
██  ██████  ██  ██  ██████  ██
██  ██████  ██  ██  ██████  ██
██  ██████  ██  ██  ██████  ██
██          ██  ██          ██
██████████████  ██████████████
                              
██  ██    ██      ██    ██  ██
    ██████    ██    ██████    
██    ████  ██  ██  ████    ██
  ██████      ██      ██████  
██    ██████████████████    ██
                              
██████████████  ██████████████
██          ██  ██          ██
██  ██████  ██  ██  ██████  ██
██  ██████  ██  ██  ██████  ██
██  ██████  ██  ██  ██████  ██
██          ██  ██          ██
██████████████  ██████████████
"""
