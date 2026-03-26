"""
Fixed NGN OTP Verification Handler with all critical correctness gaps addressed
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import SessionLocal
from models import User, SavedBankAccount, Cashout, CashoutType
from utils.decimal_precision import MonetaryDecimal
import logging

logger = logging.getLogger(__name__)

async def handle_ngn_otp_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle NGN cashout OTP verification when user enters the code - ENHANCED WITH CORRECTNESS FIXES"""
    user_id = update.effective_user.id if update.effective_user else None
    session = None
    
    try:
        if not user_id:
            logger.error("❌ No user_id available in handle_ngn_otp_verification")
            return
            
        otp_code = update.message.text.strip() if update.message and update.message.text else ""
        
        logger.info(f"🔐 VERIFYING NGN OTP - User: {user_id}, Code: [REDACTED], State: {context.user_data.get('wallet_state') if context.user_data else 'no_context'}")
        
        # COMPREHENSIVE ERROR GUARDS: Validate all required context data exists
        if not context or not context.user_data:
            await update.message.reply_text(
                "❌ **Session Expired**\n\nYour session has expired. Please start the cashout process again.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                ])
            )
            return

        cashout_data = context.user_data.get('cashout_data', {})
        verified_account = cashout_data.get('verified_account')
        cashout_amount = cashout_data.get('amount')
        rate_lock = context.user_data.get('rate_lock')
        
        # CRITICAL: Validate all required context variables exist
        missing_vars = []
        if not verified_account:
            missing_vars.append('verified_account')
        if not cashout_amount:
            missing_vars.append('cashout_amount')
        if not rate_lock:
            missing_vars.append('rate_lock')
        if not otp_code:
            missing_vars.append('otp_code')
            
        if missing_vars:
            logger.error(f"❌ Missing required context variables: {missing_vars} for user {user_id}")
            from utils.branding_utils import make_header, make_trust_footer
            header = make_header("Session Error")
            await update.message.reply_text(
                f"{header}\n\n❌ **Missing Session Data**\n\nRequired data is missing from your session.\n\nPlease start the cashout process again.\n\n{make_trust_footer()}",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Start Cashout", callback_data="wallet_cashout")],
                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                ])
            )
            return
        
        # RATE LOCK VALIDATION: Check if rate lock is still valid before proceeding
        from utils.rate_lock import RateLock
        rate_lock_validation = RateLock.validate_rate_lock(rate_lock, user_id)
        
        if not rate_lock_validation.get('valid'):
            error_code = rate_lock_validation.get('error_code', 'UNKNOWN')
            logger.warning(f"⏰ Rate lock validation failed for user {user_id}: {error_code}")
            
            from utils.branding_utils import make_header, make_trust_footer
            header = make_header("Rate Lock Expired")
            
            if error_code == 'LOCK_EXPIRED':
                expired_seconds = rate_lock_validation.get('expired_seconds', 0)
                await update.message.reply_text(
                    f"{header}\n\n⏰ **Your rate lock has expired**\n\nThe locked exchange rate expired {expired_seconds} seconds ago.\n\nPlease start a new cashout to get current rates.\n\n{make_trust_footer()}",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Start New Cashout", callback_data="wallet_cashout")],
                        [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                    ])
                )
            else:
                await update.message.reply_text(
                    f"{header}\n\n❌ **Rate Lock Invalid**\n\nYour rate lock is no longer valid.\n\nPlease start the cashout process again.\n\n{make_trust_footer()}",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Start Cashout", callback_data="wallet_cashout")],
                        [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                    ])
                )
            return
        
        # Extract validated rate lock data for processing
        locked_rate = MonetaryDecimal.to_decimal(rate_lock.get('exchange_rate'), "locked_rate")
        locked_ngn_amount = MonetaryDecimal.to_decimal(rate_lock.get('ngn_amount'), "locked_ngn_amount")
        rate_lock_token = rate_lock.get('token')
        
        logger.info(f"✅ Rate lock validated - User: {user_id}, Rate: ₦{locked_rate}, Token: {rate_lock_token[:8] if rate_lock_token else 'unknown'}...")
        
        # SESSION MANAGEMENT: Proper scoped session with rollback capability
        session = SessionLocal()
        user = None
        
        try:
            user = session.query(User).filter(User.telegram_id == int(user_id)).first()
            if not user:
                await update.message.reply_text(
                    "❌ **User Not Found**\n\nPlease try again.", 
                    parse_mode='Markdown'
                )
                return
                
            # Enhanced cashout context for OTP verification with rate lock data
            cashout_context = {
                'amount': str(cashout_amount),
                'currency': 'NGN',
                'destination_hash': f"{verified_account['bank_code']}_{verified_account['account_number']}",
                'rate_lock_token': rate_lock_token,
                'locked_rate': str(locked_rate),
                'locked_ngn_amount': str(locked_ngn_amount)
            }
            
            # IDEMPOTENCY PROTECTION: Get verification ID for deduplication
            from services.email_verification_service import EmailVerificationService
            verification_result = EmailVerificationService.verify_otp(
                user_id=user.id,
                otp_code=otp_code,
                purpose='cashout',
                cashout_context=cashout_context
            )
            
            if verification_result['success']:
                verification_id = verification_result.get('verification_id')
                logger.info(f"✅ NGN OTP verified successfully for user {user_id}, verification_id: {verification_id}")
                
                # IDEMPOTENCY CHECK: Use verification_id to prevent duplicate cashouts
                if verification_id:
                    # Check if we already processed this verification
                    existing_cashout = session.query(Cashout).filter(
                        Cashout.user_id == user.id,
                        Cashout.metadata.contains(f'"verification_id":"{verification_id}"')
                    ).first()
                    
                    if existing_cashout:
                        logger.warning(f"🔄 Duplicate OTP verification detected for user {user_id}, verification_id: {verification_id}")
                        from utils.branding_utils import make_header, format_branded_amount
                        header = make_header("Already Processing")
                        
                        await update.message.reply_text(
                            f"{header}\n\n🔄 **Cashout Already in Progress**\n\n📝 **Reference:** `{existing_cashout.cashout_id}`\n\nThis transaction is already being processed.\n\nPlease check your email for updates.",
                            parse_mode='Markdown',
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")],
                                [InlineKeyboardButton("📋 History", callback_data="wallet_history")]
                            ])
                        )
                        return
                
                # Clear OTP verification state
                context.user_data['wallet_state'] = None
                
                # Show branded processing message
                from utils.branding_utils import make_header
                header = make_header("Processing Transfer")
                await update.message.reply_text(
                    f"{header}\n\n💸 **Processing your NGN transfer...**\n\n⏳ Please wait while we send to your bank account.\n\n🔒 **Secured with rate lock:** ₦{locked_rate:,.2f}/USD",
                    parse_mode='Markdown',
                    reply_markup=None
                )
                
                # ENHANCED RATE LOCK APPLICATION: Use validated locked rate and amount
                try:
                    usd_amount = MonetaryDecimal.to_decimal(cashout_amount, "cashout_usd_amount")
                    
                    # Use the locked NGN amount directly (more accurate than recalculating)
                    ngn_amount = locked_ngn_amount
                    
                    logger.info(f"🔄 Creating NGN cashout with locked rate - User: {user_id}, ${usd_amount} → ₦{ngn_amount} (rate: ₦{locked_rate})")
                    
                    # Prepare bank destination for AutoCashoutService
                    # Format: bank_name|account_number|account_name|bank_code (required by AutoCashoutService)
                    bank_destination = f"{verified_account.get('bank_name', 'Unknown Bank')}|{verified_account['account_number']}|{verified_account.get('account_name', 'Account Holder')}|{verified_account['bank_code']}"
                    
                    # Enhanced metadata with rate lock and verification info
                    enhanced_metadata = {
                        'verification_id': verification_id,
                        'rate_lock_token': rate_lock_token,
                        'locked_rate': str(locked_rate),
                        'locked_ngn_amount': str(ngn_amount),
                        'rate_lock_expires_at': rate_lock.get('expires_at'),
                        'user_telegram_id': str(user_id),
                        'bank_name': verified_account.get('bank_name'),
                        'account_name': verified_account.get('account_name')
                    }
                    
                    # Create cashout request using AutoCashoutService with rate lock data
                    from services.auto_cashout import AutoCashoutService
                    
                    cashout_result = await AutoCashoutService.create_cashout_request(
                        user_id=user.id,
                        amount=float(usd_amount),  # AutoCashoutService expects float
                        currency="USD",  # Source currency is always USD
                        cashout_type=CashoutType.NGN_BANK.value,
                        destination=bank_destination,
                        user_ip=context.user_data.get('ip_address'),
                        user_agent=f"telegram_ngn_locked_rate_{user.id}",
                        user_initiated=True,
                        defer_processing=False,  # Process immediately
                        # CRITICAL: Pass locked rate data to ensure AutoCashoutService uses it
                        locked_exchange_rate=float(locked_rate),
                        locked_destination_amount=float(ngn_amount),
                        rate_lock_token=rate_lock_token,
                        metadata=enhanced_metadata
                    )
                    
                    if cashout_result.get('success'):
                        cashout_id = cashout_result.get('cashout_id')
                        logger.info(f"✅ Created cashout request {cashout_id} with rate lock validation, processing...")
                        
                        # ATOMIC PROCESSING: Process the approved cashout immediately with balance consistency
                        processing_result = await AutoCashoutService.process_approved_cashout(
                            cashout_id=cashout_id,
                            admin_approved=False,  # User-initiated cashout
                            force_locked_rate=True,  # Ensure locked rate is used
                            verification_context={
                                'verification_id': verification_id,
                                'rate_lock_token': rate_lock_token,
                                'user_telegram_id': str(user_id)
                            }
                        )
                        
                        if processing_result.get('success'):
                            # IMPROVED SESSION MANAGEMENT: Bank saving with proper transaction scope
                            save_bank = cashout_data.get('save_bank', False)
                            bank_save_success = True
                            
                            if save_bank:
                                # Use separate session for bank saving to prevent rollback issues
                                bank_session = SessionLocal()
                                try:
                                    # Re-query user in new session
                                    bank_user = bank_session.query(User).filter(User.telegram_id == int(user_id)).first()
                                    if bank_user:
                                        # Check if bank account already exists
                                        existing_bank = bank_session.query(SavedBankAccount).filter_by(
                                            user_id=bank_user.id,
                                            account_number=verified_account['account_number'],
                                            bank_code=verified_account['bank_code']
                                        ).first()
                                        
                                        if not existing_bank:
                                            # Create new saved bank account
                                            new_bank = SavedBankAccount(
                                                user_id=bank_user.id,
                                                account_number=verified_account['account_number'],
                                                account_name=verified_account['account_name'],
                                                bank_code=verified_account['bank_code'],
                                                bank_name=verified_account['bank_name'],
                                                is_verified=True,  # Mark as verified since OTP was successful
                                                is_default=True   # Make it default if it's the first one
                                            )
                                            bank_session.add(new_bank)
                                            bank_session.commit()
                                            logger.info(f"✅ Saved verified bank account for user {bank_user.id}: {verified_account['bank_name']}")
                                        else:
                                            # Mark existing as verified and default
                                            existing_bank.is_verified = True
                                            existing_bank.is_default = True
                                            bank_session.commit()
                                            logger.info(f"✅ Updated bank account verification for user {bank_user.id}")
                                    else:
                                        logger.error(f"❌ User not found in bank session for user {user_id}")
                                        bank_save_success = False
                                        
                                except Exception as save_error:
                                    logger.error(f"⚠️ Error saving bank account: {save_error}")
                                    bank_session.rollback()
                                    bank_save_success = False
                                    # Don't fail the cashout for bank saving issues
                                finally:
                                    bank_session.close()
                            
                            # SUCCESS: Show completion message with branding
                            from utils.branding_utils import make_header, make_trust_footer, format_branded_amount
                            header = make_header("Transfer Complete")
                            usd_formatted = format_branded_amount(float(usd_amount), "USD")
                            ngn_formatted = format_branded_amount(float(ngn_amount), "NGN")
                            
                            bank_save_note = "\n🏦 **Bank saved:** Account saved for future cashouts" if save_bank and bank_save_success else ""
                            
                            success_text = f"""{header}

✅ **NGN Transfer Completed!**

📝 **Reference:** `{cashout_id}`
💰 **Amount:** {usd_formatted} → {ngn_formatted}
🔒 **Rate Used:** ₦{locked_rate:,.2f}/USD (locked)
🏦 **Bank:** {verified_account['bank_name']}
👤 **Account:** {verified_account['account_name']}
💳 **Number:** {verified_account['account_number']}
🔄 **Status:** Transfer completed successfully{bank_save_note}

📧 Check your email for full transaction details.

{make_trust_footer()}"""
                            
                            await update.message.reply_text(
                                success_text,
                                parse_mode='Markdown',
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")],
                                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                                ])
                            )
                            
                            # Clear cashout data and invalidate rate lock
                            context.user_data.pop('cashout_data', None)
                            RateLock.invalidate_rate_lock(rate_lock, "cashout_completed")
                            context.user_data.pop('rate_lock', None)
                            
                        else:
                            # ATOMIC PROCESSING FAILED: Handle balance consistency
                            error_msg = processing_result.get('error', 'Processing failed')
                            logger.error(f"❌ NGN cashout processing failed: {error_msg}")
                            
                            # Check if AutoCashoutService already handled refund
                            refund_status = processing_result.get('refund_status', 'unknown')
                            
                            from utils.branding_utils import make_header, make_trust_footer
                            header = make_header("Transfer Failed")
                            
                            await update.message.reply_text(
                                f"{header}\n\n❌ **Transfer Processing Failed**\n\n📝 **Reference:** `{cashout_id}`\n🔄 **Status:** Failed - {error_msg}\n💰 **Refund:** {refund_status}\n\n💡 Your funds are safe. Please try again or contact support.\n\n{make_trust_footer()}",
                                parse_mode='Markdown',
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")],
                                    [InlineKeyboardButton("💬 Support", callback_data="support_chat")]
                                ])
                            )
                    else:
                        # Cashout request creation failed
                        error_msg = cashout_result.get('error', 'Failed to create cashout request')
                        logger.error(f"❌ NGN cashout request creation failed: {error_msg}")
                        
                        from utils.branding_utils import make_header, make_trust_footer
                        header = make_header("Cashout Failed")
                        
                        await update.message.reply_text(
                            f"{header}\n\n❌ **Cashout Request Failed**\n\n🔄 **Status:** {error_msg}\n\n💡 Please try again or contact support if the issue persists.\n\n{make_trust_footer()}",
                            parse_mode='Markdown',
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")],
                                [InlineKeyboardButton("💬 Support", callback_data="support_chat")]
                            ])
                        )
                
                except Exception as processing_error:
                    logger.error(f"❌ CRITICAL: NGN cashout processing exception: {processing_error}")
                    
                    from utils.branding_utils import make_header, make_trust_footer
                    header = make_header("System Error")
                    
                    await update.message.reply_text(
                        f"{header}\n\n❌ **Processing Error**\n\nThere was an unexpected error processing your cashout.\n\n💡 Your funds are safe. Please try again or contact support.\n\n**Error:** {str(processing_error)[:100]}...\n\n{make_trust_footer()}",
                        parse_mode='Markdown',
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")],
                            [InlineKeyboardButton("💬 Support", callback_data="support_chat")]
                        ])
                    )
                
            else:
                # OTP VERIFICATION FAILED: Enhanced error messages with branding
                error_msg = verification_result.get('message', 'Invalid verification code.')
                remaining_attempts = verification_result.get('attempts_remaining')
                
                from utils.branding_utils import make_header
                
                if remaining_attempts and remaining_attempts > 0:
                    header = make_header("Code Verification")
                    await update.message.reply_text(
                        f"{header}\n\n❌ **{error_msg}**\n\n🔄 {remaining_attempts} attempts remaining.\n\nPlease enter the correct 6-digit code:",
                        parse_mode='Markdown'
                    )
                else:
                    # Max attempts exceeded, restart process and invalidate rate lock
                    context.user_data['wallet_state'] = None
                    RateLock.invalidate_rate_lock(rate_lock, "max_attempts_exceeded")
                    
                    header = make_header("Verification Failed")
                    await update.message.reply_text(
                        f"{header}\n\n❌ **{error_msg}**\n\nMaximum attempts exceeded. Please start the cashout process again.",
                        parse_mode='Markdown',
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔄 Start Over", callback_data="wallet_cashout")],
                            [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                        ])
                    )
                    
        except Exception as session_error:
            logger.error(f"❌ Session error in handle_ngn_otp_verification: {session_error}")
            if session:
                session.rollback()
        finally:
            if session:
                session.close()
            
    except Exception as e:
        logger.error(f"❌ Critical error in handle_ngn_otp_verification: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        
        from utils.branding_utils import make_header, make_trust_footer
        header = make_header("System Error")
        
        try:
            await update.message.reply_text(
                f"{header}\n\n❌ **Verification Error**\n\nThere was an error verifying your code.\n\nPlease try again or contact support.\n\n{make_trust_footer()}",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")],
                    [InlineKeyboardButton("💬 Support", callback_data="support_chat")]
                ])
            )
        except Exception as msg_error:
            logger.error(f"❌ Failed to send error message: {msg_error}")