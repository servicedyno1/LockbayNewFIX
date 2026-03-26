"""Enhanced UI Components - Standardized button placement and UX improvements"""

from typing import List
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from services.enhanced_cancellation_service import EnhancedCancellationService, CancellationStage

class EnhancedUIComponents:
    """Standardized UI components with enhanced cancellation UX"""
    
    @classmethod
    def build_payment_method_keyboard(
        cls, 
        wallet_balance_text: str, 
        include_back: bool = True, 
        back_callback: str = "back", 
        operation_type: str = "escrow",
        stage: str = CancellationStage.MIDDLE
    ) -> InlineKeyboardMarkup:
        """Build standardized payment method selection keyboard"""
        
        keyboard = []
        
        # Wallet option (if user has balance)
        keyboard.append([InlineKeyboardButton(wallet_balance_text, callback_data="payment_wallet")])
        
        # Crypto options (2x3 grid layout)
        keyboard.extend([
            [
                InlineKeyboardButton("₿ BTC", callback_data="crypto_BTC"),
                InlineKeyboardButton("Ξ ETH", callback_data="crypto_ETH"),
                InlineKeyboardButton("Ł LTC", callback_data="crypto_LTC"),
            ],
            [
                InlineKeyboardButton("Ð DOGE", callback_data="crypto_DOGE"),
                InlineKeyboardButton("◊ TRX", callback_data="crypto_TRX"),
            ],
            [
                InlineKeyboardButton("Ƀ BCH", callback_data="crypto_BCH"),
                InlineKeyboardButton("₮ USDT-ERC20", callback_data="crypto_USDT"),
                InlineKeyboardButton("₮ USDT-TRC20", callback_data="crypto_USDT-TRC20"),
            ],
            [InlineKeyboardButton("🇳🇬 Bank Transfer", callback_data="payment_ngn")],
        ])
        
        # Navigation row
        if include_back:
            keyboard.append([InlineKeyboardButton("⬅️ Back to Fee Options", callback_data=back_callback)])
        
        # Standardized cancel button (last row)
        keyboard = EnhancedCancellationService.add_standardized_cancel_row(keyboard, operation_type, stage)
        
        return InlineKeyboardMarkup(keyboard)
    
    @classmethod 
    def build_amount_selection_keyboard(
        cls,
        operation_type: str = "escrow",
        stage: str = CancellationStage.EARLY,
        include_custom: bool = True,
        custom_amounts: List[int] = None
    ) -> InlineKeyboardMarkup:
        """Build standardized amount selection keyboard"""
        
        if custom_amounts is None:
            custom_amounts = [100, 500, 1000]
        
        keyboard = []
        
        # Amount options
        amount_buttons = []
        for amount in custom_amounts:
            amount_buttons.append(
                InlineKeyboardButton(f"💰 ${amount}", callback_data=f"amount_{amount}")
            )
        
        # Arrange in rows of 2
        for i in range(0, len(amount_buttons), 2):
            keyboard.append(amount_buttons[i:i+2])
        
        # Custom amount option
        if include_custom:
            keyboard.append([InlineKeyboardButton("💭 Custom Amount", callback_data="amount_custom")])
        
        # Navigation buttons
        keyboard.append([
            InlineKeyboardButton("🔙 Back", callback_data="back"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
        ])
        
        # Standardized cancel button (last row)
        keyboard = EnhancedCancellationService.add_standardized_cancel_row(keyboard, operation_type, stage)
        
        return InlineKeyboardMarkup(keyboard)
    
    @classmethod
    def build_confirmation_keyboard(
        cls,
        operation_type: str,
        include_edit: bool = True,
        edit_options: List[str] = None,
        stage: str = CancellationStage.ADVANCED
    ) -> InlineKeyboardMarkup:
        """Build standardized confirmation keyboard with enhanced cancellation"""
        
        keyboard = []
        
        # Main confirmation button
        if operation_type == "escrow":
            keyboard.append([InlineKeyboardButton("✅ Create Trade", callback_data="confirm_escrow")])
        elif operation_type == "exchange":
            keyboard.append([InlineKeyboardButton("✅ Confirm Order", callback_data="exchange_confirm_order")])
        
        # Edit options
        if include_edit and edit_options:
            edit_buttons = []
            for option in edit_options[:2]:  # Max 2 edit options per row for mobile
                if option == "crypto":
                    edit_buttons.append(InlineKeyboardButton("🔄 Change Crypto", callback_data=f"{operation_type}_crypto_switch_pre"))
                elif option == "bank":
                    edit_buttons.append(InlineKeyboardButton("🏦 Change Bank", callback_data=f"{operation_type}_bank_switch_pre"))
                elif option == "amount":
                    edit_buttons.append(InlineKeyboardButton("💰 Edit Amount", callback_data=f"{operation_type}_edit_amount"))
            
            if edit_buttons:
                keyboard.append(edit_buttons)
        
        # Help option for advanced operations
        keyboard.append([InlineKeyboardButton("📞 Get Help", callback_data="contact_support")])
        
        # Standardized cancel button (last row) - advanced stage for confirmation
        keyboard = EnhancedCancellationService.add_standardized_cancel_row(keyboard, operation_type, stage)
        
        return InlineKeyboardMarkup(keyboard)
    
    @classmethod
    def build_payment_instructions_keyboard(
        cls,
        operation_type: str,
        include_qr: bool = True,
        include_switch: bool = True,
        stage: str = CancellationStage.ADVANCED
    ) -> InlineKeyboardMarkup:
        """Build standardized payment instructions keyboard"""
        
        keyboard = []
        
        # QR code and copy options  
        if include_qr:
            keyboard.append([InlineKeyboardButton("📱 QR Code", callback_data="show_qr")])
        
        # Switch options
        if include_switch:
            keyboard.append([InlineKeyboardButton("🔄 Switch Payment", callback_data="back_to_payment")])
        
        # Help option
        keyboard.append([InlineKeyboardButton("📞 Get Help", callback_data="contact_support")])
        
        # Main menu and standardized cancel (separate rows for clarity)
        keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
        
        # Standardized cancel button (last row) - advanced stage for payment processing
        keyboard = EnhancedCancellationService.add_standardized_cancel_row(keyboard, operation_type, stage)
        
        return InlineKeyboardMarkup(keyboard)
    
    @classmethod
    def build_error_recovery_keyboard(
        cls,
        operation_type: str,
        include_retry: bool = True,
        include_help: bool = True,
        stage: str = CancellationStage.MIDDLE
    ) -> InlineKeyboardMarkup:
        """Build standardized error recovery keyboard"""
        
        keyboard = []
        
        # Retry option
        if include_retry:
            keyboard.append([InlineKeyboardButton("🔄 Try Again", callback_data=f"retry_{operation_type}")])
        
        # Help option
        if include_help:
            keyboard.append([InlineKeyboardButton("📞 Get Help", callback_data="contact_support")])
        
        # Main menu
        keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
        
        # Standardized cancel button (last row)
        keyboard = EnhancedCancellationService.add_standardized_cancel_row(keyboard, operation_type, stage)
        
        return InlineKeyboardMarkup(keyboard)
    
    @classmethod
    def format_context_aware_error(
        cls,
        error_type: str,
        operation_type: str,
        stage: str,
        user_friendly: bool = True
    ) -> str:
        """Format context-aware error messages"""
        
        base_messages = {
            "rate_fetch_failed": {
                "escrow": {
                    CancellationStage.EARLY: "🔄 Checking current rates...",
                    CancellationStage.MIDDLE: "⚠️ Rate update needed. Your trade details are saved.",
                    CancellationStage.ADVANCED: "⚠️ Unable to get latest rates. Your setup is secure."
                },
                "exchange": {
                    CancellationStage.EARLY: "🔄 Loading exchange rates...",
                    CancellationStage.MIDDLE: "⚠️ Rate refresh needed. Your order details are saved.",
                    CancellationStage.ADVANCED: "⚠️ Rate temporarily unavailable. Your funds are safe."
                }
            },
            "payment_failed": {
                "escrow": {
                    CancellationStage.ADVANCED: "❌ Payment setup failed. No charges applied to your account."
                },
                "exchange": {
                    CancellationStage.ADVANCED: "❌ Payment processing failed. Your funds remain secure."
                }
            },
            "network_error": {
                "escrow": {
                    CancellationStage.EARLY: "🌐 Connection issue. Please try again.",
                    CancellationStage.MIDDLE: "🌐 Network error. Your progress is saved.",
                    CancellationStage.ADVANCED: "🌐 Connection issue. Your setup is secure."
                },
                "exchange": {
                    CancellationStage.EARLY: "🌐 Connection issue. Please try again.", 
                    CancellationStage.MIDDLE: "🌐 Network error. Your order data is saved.",
                    CancellationStage.ADVANCED: "🌐 Connection issue. Your funds are safe."
                }
            }
        }
        
        try:
            message = base_messages[error_type][operation_type][stage]
        except KeyError:
            # Fallback message
            message = f"⚠️ {error_type.replace('_', ' ').title()}. Please try again."
        
        if user_friendly:
            # Add reassuring context
            if stage == CancellationStage.ADVANCED:
                message += "\n\n🔒 Your account and funds remain secure."
            elif stage == CancellationStage.MIDDLE:
                message += "\n\n💾 Your progress has been saved."
        
        return message