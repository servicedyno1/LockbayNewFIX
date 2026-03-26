"""
Contact Management Handler
Unified interface for users to manage multiple contact methods and notification preferences
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
)
from database import SessionLocal
from models import User, UserContact
from services.contact_detection_service import contact_detection_service
from config import Config

# PERFORMANCE OPTIMIZATION: Leverage wallet_prefetch for saved destinations (18 queries → 0 cached)
from utils.wallet_prefetch import get_cached_wallet_data

# SMS service removed
from utils.helpers import generate_unique_id
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query
from utils.keyboards import main_menu_keyboard
import phonenumbers

logger = logging.getLogger(__name__)

# Conversation states
(
    CONTACT_MENU,
    ADD_CONTACT_TYPE,
    ADD_CONTACT_VALUE,
    VERIFY_CONTACT,
    NOTIFICATION_PREFERENCES,
) = range(5)

class ContactManagementHandler:
    """Handler for contact management and notification preferences"""

    def __init__(self):
        # Remove persistent session - use context managers instead
        pass

    async def contact_management_menu(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Show contact management main menu
        
        OPTIMIZATION: Leverages wallet_prefetch cache for saved destinations (18 queries → 0 cached)
        """
        try:
            query = update.callback_query
            if query:
                # IMMEDIATE FEEDBACK: Contact management access
                await safe_answer_callback_query(query, "📞 Contact management")

            user_id = update.effective_user.id if update.effective_user else 0

            # PERFORMANCE OPTIMIZATION: Check wallet cache first for saved destinations
            cached_wallet = get_cached_wallet_data(context.user_data)
            if cached_wallet:
                logger.info("✅ CONTACT_CACHE_HIT: Using cached wallet data for saved destinations (0 queries)")
                len(cached_wallet.get('saved_crypto_addresses', []))
                len(cached_wallet.get('saved_bank_accounts', []))
            else:
                logger.debug("ℹ️ CONTACT_CACHE_MISS: Wallet cache not available, will query database if needed")

            # Get user's current contacts
            session = SessionLocal()
            try:
                from utils.repository import UserRepository
                user = UserRepository.get_user_by_telegram_id(session, user_id)
                if not user:
                    await safe_edit_message_text(
                        query or update.message,
                        "❌ User not found. Use /start first.",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "🏠 Main Menu", callback_data="main_menu"
                                    )
                                ]
                            ]
                        ),
                    )
                    # Graceful recovery - return to main menu (without passing undefined user/closed session)
                    from handlers.start import show_main_menu
                    await show_main_menu(update, context)
                    return ConversationHandler.END

                # Get all linked contacts
                contact_detection_service.get_all_linked_contacts(
                    getattr(user, "id", 0)
                )

                # Build contact list text - more concise
                contact_text = "📱 Contacts\n\n"

                # Primary contacts
                if getattr(user, "telegram_id", None):
                    contact_text += "📱 Telegram ✅\n"

                if getattr(user, "email", None):
                    status = "✅" if getattr(user, "email_verified", False) else "⏳"
                    email_display = (
                        str(getattr(user, "email", ""))[:Config.EMAIL_PREVIEW_LENGTH] + "..."
                        if len(str(getattr(user, "email", ""))) > Config.EMAIL_PREVIEW_LENGTH
                        else str(getattr(user, "email", ""))
                    )
                    contact_text += f"📧 {email_display} {status}\n"

                if getattr(user, "phone_number", None):
                    status = "✅" if getattr(user, "phone_verified", False) else "⏳"
                    phone_str = str(getattr(user, "phone_number", ""))
                    phone_display = (
                        phone_str[:6] + "••••" + phone_str[-3:]
                        if len(phone_str) > 8
                        else phone_str
                    )
                    contact_text += f"📞 {phone_display} {status}\n"

                # Notification preferences
                preferences = getattr(user, "notification_preferences", {}) or {}
                multi_channel = preferences.get("multi_channel_notifications", True)
                primary_channel = (
                    getattr(user, "primary_notification_channel", "telegram") or "telegram"
                )

                contact_text += "\n🔔 Notifications\n"
                contact_text += f"Primary: {primary_channel.title()}\n"
                contact_text += f"Multi-Channel: {'✅' if multi_channel else '❌'}\n"

                keyboard = [
                    [InlineKeyboardButton("➕ Add Contact", callback_data="add_contact")],
                    [
                        InlineKeyboardButton(
                            "📧 Verify Email", callback_data="verify_email"
                        ),
                        InlineKeyboardButton(
                            "📱 Verify Phone", callback_data="verify_phone"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "🔔 Settings", callback_data="notification_settings"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "📊 All Contacts", callback_data="view_all_contacts"
                        )
                    ],
                    [InlineKeyboardButton("🏠 Menu", callback_data="main_menu")],
                ]

                await safe_edit_message_text(
                    query or update.message,
                    contact_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

                return CONTACT_MENU
            except Exception as e:
                logger.error(f"Error in contact management menu: {e}")
                # Graceful recovery - return to main menu
                from handlers.start import show_main_menu
                await show_main_menu(update, context)
                return ConversationHandler.END
            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error in contact management menu: {e}")
            query = update.callback_query if update.callback_query else None
            await safe_edit_message_text(
                query or update.message,
                "❌ Error loading contact information. Please try again.",
                reply_markup=main_menu_keyboard(),  # Simple fallback for contact management
            )
            return ConversationHandler.END

    async def add_contact_type_menu(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Show menu to select contact type to add"""
        try:
            query = update.callback_query
            if query:
                # IMMEDIATE FEEDBACK: Add contact type
                await safe_answer_callback_query(query, "➕ Add contact type")

            keyboard = [
                [InlineKeyboardButton("📧 Add Email", callback_data="add_email")],
                [InlineKeyboardButton("📱 Add Phone", callback_data="add_phone")],
                [InlineKeyboardButton("🔙 Back", callback_data="contact_menu")],
            ]

            await safe_edit_message_text(
                query,
                "📋 Add New Contact Method\n\nSelect the type of contact you want to add:",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

            return ADD_CONTACT_TYPE

        except Exception as e:
            logger.error(f"Error in add contact type menu: {e}")
            return ConversationHandler.END

    async def add_contact_value(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handle contact type selection and request value"""
        try:
            query = update.callback_query
            if not query:
                # Graceful recovery - return to main menu
                from handlers.start import show_main_menu
                await show_main_menu(update, context)
                return ConversationHandler.END
            # IMMEDIATE FEEDBACK: Contact action
            await safe_answer_callback_query(query, "📞 Contact action")

            contact_type = None
            prompt_text = ""

            if query.data == "add_email":
                contact_type = "email"
                prompt_text = "📧 Add Email\n\nEnter your email:"
            elif query.data == "add_phone":
                contact_type = "phone"
                prompt_text = (
                    f"📱 Add Phone\n\nEnter with country code (e.g., {Config.EXAMPLE_PHONE_NUMBER}):"
                )

            if not contact_type:
                await safe_edit_message_text(
                    query,
                    "❌ Invalid selection. Please try again.",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔙 Back", callback_data="contact_menu"
                                )
                            ]
                        ]
                    ),
                )
                return CONTACT_MENU

            # Store contact type in context
            if context.user_data is not None:
                context.user_data["adding_contact_type"] = contact_type

            keyboard = [
                [InlineKeyboardButton("❌ Cancel", callback_data="contact_menu")]
            ]

            await safe_edit_message_text(
                query,
                prompt_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

            return ADD_CONTACT_VALUE

        except Exception as e:
            logger.error(f"Error in add contact value: {e}")
            return ConversationHandler.END

    async def process_contact_value(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Process entered contact value and initiate verification"""
        session = SessionLocal()
        try:
            user_id = (
                getattr(update.effective_user, "id", 0) if update.effective_user else 0
            )
            # PRODUCTION FIX: Check for conversation conflicts
            from utils.conversation_isolation import check_conversation_conflict
            if not check_conversation_conflict(context, "contact_management"):
                # Graceful recovery - return to main menu
                from handlers.start import show_main_menu
                await show_main_menu(update, context)
                return ConversationHandler.END
                
            # Secure input validation and sanitization
            if update.message and update.message.text:
                from utils.enhanced_input_validation import SecurityInputValidator
                
                validation_result = SecurityInputValidator.validate_and_sanitize_input(
                    update.message.text,
                    "contact_value",
                    max_length=100,
                    required=True
                )
                
                if not validation_result["is_valid"]:
                    if update.message:
                        await update.message.reply_text(
                            "❌ Invalid input detected. Please check your input and try again.",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🔙 Back", callback_data="contact_menu")]
                            ])
                        )
                    return ADD_CONTACT_VALUE
                
                contact_value = validation_result["sanitized_value"].strip()
            else:
                contact_value = ""
            contact_type = (
                context.user_data.get("adding_contact_type")
                if context.user_data
                else None
            )

            if not contact_type:
                if update.message:
                    await update.message.reply_text(
                        "❌ Session expired. Please start again.",
                        reply_markup=main_menu_keyboard(),  # Simple fallback for contact management
                    )
                # Graceful recovery - return to main menu
                from handlers.start import show_main_menu
                await show_main_menu(update, context)
                return ConversationHandler.END

            # Validate contact value
            validation_result = self._validate_contact_value(
                contact_value, contact_type
            )
            if not validation_result["valid"]:
                keyboard = [
                    [InlineKeyboardButton("🔙 Back", callback_data="contact_menu")]
                ]
                if update.message:
                    await update.message.reply_text(
                        f"❌ {validation_result['error']}\n\nPlease try again:",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                return ADD_CONTACT_VALUE

            # Normalize contact value
            normalized_value = validation_result["normalized"]

            # Check if contact already exists
            session = SessionLocal()
            try:
                from utils.repository import UserRepository
                user = UserRepository.get_user_by_telegram_id(session, user_id)
                if not user:
                    if update.message:
                        await update.message.reply_text(
                            "❌ User not found. Please start the bot first.",
                            reply_markup=main_menu_keyboard(),  # Simple fallback for contact management
                        )
                    # Graceful recovery - return to main menu
                    from handlers.start import show_main_menu
                    await show_main_menu(update, context)
                    return ConversationHandler.END

                # Check for duplicates
                if self._contact_already_exists(
                    getattr(user, "id", 0), contact_type, normalized_value
                ):
                    if update.message:
                        await update.message.reply_text(
                            f"⚠️ This {contact_type} is already linked to your account.",
                            reply_markup=InlineKeyboardMarkup(
                                [
                                    [
                                        InlineKeyboardButton(
                                            "🔙 Back", callback_data="contact_menu"
                                        )
                                    ]
                                ]
                            ),
                        )
                    return CONTACT_MENU

                # Create new contact entry
                contact_id = generate_unique_id("contact")
                verification_code = self._generate_verification_code()

                new_contact = UserContact(
                    contact_id=contact_id,
                    user_id=getattr(user, "id", 0),
                    contact_type=contact_type,
                    contact_value=normalized_value,
                    verification_code=verification_code,
                    verification_expires=datetime.utcnow() + timedelta(minutes=Config.OTP_EXPIRY_MINUTES),
                    verification_attempts=0,
                )

                session.add(new_contact)
                session.commit()

                # Send verification code
                success = await self._send_verification_code(
                    contact_type, normalized_value, verification_code
                )

                if success:
                    # Store contact ID for verification
                    if context.user_data is not None:
                        context.user_data["verifying_contact_id"] = contact_id

                    keyboard = [
                        [
                            InlineKeyboardButton(
                                "✅ Enter Code", callback_data="enter_verification_code"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "🔄 Resend Code", callback_data="resend_verification"
                            )
                        ],
                        [InlineKeyboardButton("❌ Cancel", callback_data="contact_menu")],
                    ]

                    if update.message:
                        await update.message.reply_text(
                            f"📧 Verification Sent\n\n"
                            f"We've sent a verification code to your {contact_type}:\n"
                            f"`{normalized_value}`\n\n"
                            f"⏰ Code expires in {Config.OTP_EXPIRY_MINUTES} minutes",
                            reply_markup=InlineKeyboardMarkup(keyboard),
                        )

                    return VERIFY_CONTACT
                else:
                    # Remove failed contact
                    session.delete(new_contact)
                    session.commit()
                    
                    if update.message:
                        await update.message.reply_text(
                            f"❌ Failed to send verification to {normalized_value}. Please check the {contact_type} and try again.",
                            reply_markup=InlineKeyboardMarkup(
                                [
                                    [
                                        InlineKeyboardButton(
                                            "🔙 Back", callback_data="contact_menu"
                                        )
                                    ]
                                ]
                            ),
                        )
                    return CONTACT_MENU

            except Exception as e:
                logger.error(f"Error creating contact: {e}")
                if update.message:
                    await update.message.reply_text(
                        "❌ Error creating contact. Please try again.",
                        reply_markup=InlineKeyboardMarkup(
                            [[InlineKeyboardButton("🔙 Back", callback_data="contact_menu")]]
                        ),
                    )
                return CONTACT_MENU
            finally:
                if 'session' in locals():
                    session.close()

        except Exception as outer_e:
            logger.error(f"Outer error in add_contact_input: {outer_e}")
            return CONTACT_MENU

    async def verification_menu(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Show verification code entry interface"""
        session = SessionLocal()
        try:
            query = update.callback_query
            if not query:
                # Graceful recovery - return to main menu
                from handlers.start import show_main_menu
                await show_main_menu(update, context)
                return ConversationHandler.END
            # IMMEDIATE FEEDBACK: Contact action
            await safe_answer_callback_query(query, "📞 Contact action")

            if query.data == "enter_verification_code":
                await safe_edit_message_text(
                    query,
                    "🔐 Enter Verification Code\n\nPlease enter the verification code you received:",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "❌ Cancel", callback_data="contact_menu"
                                )
                            ]
                        ]
                    ),
                )
                return VERIFY_CONTACT

            elif query.data == "resend_verification":
                # Resend verification code
                contact_id = (
                    context.user_data.get("verifying_contact_id")
                    if context.user_data
                    else None
                )
                if contact_id:
                    contact = (
                        session.query(UserContact)
                        .filter(UserContact.contact_id == contact_id)
                        .first()
                    )

                    if contact:
                        # Generate new code
                        verification_code = self._generate_verification_code()
                        # Update verification data via SQL to avoid Column assignment issues
                        from sqlalchemy import update as sql_update

                        session.execute(
                            sql_update(UserContact)
                            .where(UserContact.id == contact.id)
                            .values(
                                verification_code=verification_code,
                                verification_expires=datetime.utcnow()
                                + timedelta(minutes=Config.OTP_EXPIRY_MINUTES),
                                verification_attempts=0,
                            )
                        )
                        session.commit()

                        # Send new code
                        success = await self._send_verification_code(
                            str(contact.contact_type),
                            str(contact.contact_value),
                            verification_code,
                        )

                        if success:
                            await safe_edit_message_text(
                                query,
                                f"📧 Verification Code Resent\n\n"
                                f"New code sent to your {contact.contact_type}:\n"
                                f"`{contact.contact_value}`\n\n"
                                f"⏰ Code expires in {Config.OTP_EXPIRY_MINUTES} minutes",
                                reply_markup=InlineKeyboardMarkup(
                                    [
                                        [
                                            InlineKeyboardButton(
                                                "✅ Enter Code",
                                                callback_data="enter_verification_code",
                                            )
                                        ],
                                        [
                                            InlineKeyboardButton(
                                                "❌ Cancel",
                                                callback_data="contact_menu",
                                            )
                                        ],
                                    ]
                                ),
                            )
                        else:
                            await safe_edit_message_text(
                                query,
                                "❌ Failed to resend verification code. Please try again later.",
                                reply_markup=InlineKeyboardMarkup(
                                    [
                                        [
                                            InlineKeyboardButton(
                                                "🔙 Back", callback_data="contact_menu"
                                            )
                                        ]
                                    ]
                                ),
                            )

                return VERIFY_CONTACT

        except Exception as e:
            logger.error(f"Error in verification menu: {e}")
            # Graceful recovery - return to main menu
            from handlers.start import show_main_menu
            return await show_main_menu(update, context)
        finally:
            if 'session' in locals():
                session.close()

        # Default return for all code paths
        return VERIFY_CONTACT

    async def process_verification_code(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Process entered verification code"""
        session = SessionLocal()
        try:
            verification_code = (
                update.message.text.strip()
                if update.message and update.message.text
                else ""
            )
            contact_id = (
                context.user_data.get("verifying_contact_id")
                if context.user_data
                else None
            )

            if not contact_id:
                if update.message:
                    await update.message.reply_text(
                        "❌ Session expired. Please start again.",
                        reply_markup=main_menu_keyboard(),  # Simple fallback for contact management
                    )
                # Graceful recovery - return to main menu
                from handlers.start import show_main_menu
                await show_main_menu(update, context)
                return ConversationHandler.END

            # Get contact
            contact = (
                session.query(UserContact)
                .filter(UserContact.contact_id == contact_id)
                .first()
            )

            if not contact:
                if update.message:
                    await update.message.reply_text(
                        "❌ Verification session not found. Please try again.",
                        reply_markup=main_menu_keyboard(),  # Simple fallback for contact management
                    )
                # Graceful recovery - return to main menu
                from handlers.start import show_main_menu
                await show_main_menu(update, context)
                return ConversationHandler.END

            # Check if code expired
            expires_time = getattr(contact, "verification_expires", None)
            if expires_time and datetime.utcnow() > expires_time:
                if update.message:
                    await update.message.reply_text(
                        "⏰ Verification code expired. Please request a new one.",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "🔄 Resend Code",
                                        callback_data="resend_verification",
                                    )
                                ],
                                [
                                    InlineKeyboardButton(
                                        "❌ Cancel", callback_data="contact_menu"
                                    )
                                ],
                            ]
                        ),
                    )
                return VERIFY_CONTACT

            # Check verification attempts
            attempts = getattr(contact, "verification_attempts", 0)
            if attempts is not None and attempts >= 3:
                if update.message:
                    await update.message.reply_text(
                        "❌ Too many verification attempts. Please request a new code.",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "🔄 Resend Code",
                                        callback_data="resend_verification",
                                    )
                                ],
                                [
                                    InlineKeyboardButton(
                                        "❌ Cancel", callback_data="contact_menu"
                                    )
                                ],
                            ]
                        ),
                    )
                return VERIFY_CONTACT

            # Verify code
            if verification_code == contact.verification_code:
                # Success - verify contact
                # Update verification status via SQL to avoid Column assignment issues
                from sqlalchemy import update as sql_update

                session.execute(
                    sql_update(UserContact)
                    .where(UserContact.id == contact.id)
                    .values(
                        is_verified=True,
                        verified_at=datetime.utcnow(),
                        verification_code=None,
                        verification_expires=None,
                    )
                )
                session.commit()

                # Clear verification data
                if context.user_data is not None:
                    context.user_data.pop("verifying_contact_id", None)
                    context.user_data.pop("adding_contact_type", None)

                if update.message:
                    await update.message.reply_text(
                        f"✅ Contact Verified Successfully!\n\n"
                        f"Your {contact.contact_type} has been added and verified:\n"
                        f"`{contact.contact_value}`\n\n"
                        f"You'll now receive notifications on this channel when enabled.",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "📋 Manage Contacts",
                                        callback_data="contact_menu",
                                    )
                                ],
                                [
                                    InlineKeyboardButton(
                                        "🏠 Main Menu", callback_data="main_menu"
                                    )
                                ],
                            ]
                        ),
                    )

                # Graceful recovery - return to main menu
                from handlers.start import show_main_menu
                await show_main_menu(update, context)
                return ConversationHandler.END
            else:
                # Wrong code - update via SQL to avoid Column assignment issues
                from sqlalchemy import update as sql_update

                session.execute(
                    sql_update(UserContact)
                    .where(UserContact.id == contact.id)
                    .values(verification_attempts=UserContact.verification_attempts + 1)
                )
                session.commit()

                remaining_attempts = 3 - contact.verification_attempts

                if update.message:
                    await update.message.reply_text(
                        f"❌ Incorrect Verification Code\n\n"
                        f"Attempts remaining: {remaining_attempts}\n\n"
                        f"Please enter the correct code:",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "🔄 Resend Code",
                                        callback_data="resend_verification",
                                    )
                                ],
                                [
                                    InlineKeyboardButton(
                                        "❌ Cancel", callback_data="contact_menu"
                                    )
                                ],
                            ]
                        ),
                    )

                return VERIFY_CONTACT

        except Exception as e:
            logger.error(f"Error processing verification code: {e}")
            if "session" in locals():
                session.rollback()
            if update.message:
                await update.message.reply_text(
                    "❌ Error verifying code. Please try again.",
                    reply_markup=main_menu_keyboard(),  # Simple fallback for contact management
                )
            return ConversationHandler.END
        finally:
            if "session" in locals():
                session.close()

    async def notification_preferences_menu(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Show notification preferences interface"""
        session = SessionLocal()
        try:
            query = update.callback_query
            if not query:
                # Graceful recovery - return to main menu
                from handlers.start import show_main_menu
                await show_main_menu(update, context)
                return ConversationHandler.END
            # IMMEDIATE FEEDBACK: Contact action
            await safe_answer_callback_query(query, "📞 Contact action")

            user_id = (
                getattr(update.effective_user, "id", 0) if update.effective_user else 0
            )
            from utils.repository import UserRepository
            user = UserRepository.get_user_by_telegram_id(session, user_id)

            if not user:
                await safe_edit_message_text(
                    query,
                    "❌ User not found. Please start the bot first.",
                    reply_markup=main_menu_keyboard(),  # Simple fallback for contact management
                )
                # Graceful recovery - return to main menu
                from handlers.start import show_main_menu
                await show_main_menu(update, context)
                return ConversationHandler.END

            preferences = getattr(user, "notification_preferences", {}) or {}
            multi_channel = preferences.get("multi_channel_notifications", True)
            primary_channel = (
                getattr(user, "primary_notification_channel", "telegram") or "telegram"
            )

            text = "🔔 Notification Preferences\n\n"
            text += f"Primary Channel: {primary_channel.title()}\n"
            text += f"Multi-Channel Notifications: {'✅ Enabled' if multi_channel else '❌ Disabled'}\n\n"

            text += "How it works:\n"
            text += "• Primary channel gets notifications first\n"
            text += (
                "• Multi-channel sends to all verified contacts for important alerts\n"
            )
            text += "• Smart routing uses your fastest-responding channel\n\n"

            text += "Channel Performance:\n"
            text += f"📱 Telegram: {user.telegram_response_time_avg:.1f}h avg\n"
            text += f"📧 Email: {user.email_response_time_avg:.1f}h avg\n"
            text += f"📲 SMS: {user.sms_response_time_avg:.1f}h avg\n"

            keyboard = [
                [
                    InlineKeyboardButton(
                        f"📱 Set Primary: Telegram {'✅' if primary_channel == 'telegram' else ''}",
                        callback_data="set_primary_telegram",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"📧 Set Primary: Email {'✅' if primary_channel == 'email' else ''}",
                        callback_data="set_primary_email",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"📲 Set Primary: SMS {'✅' if primary_channel == 'sms' else ''}",
                        callback_data="set_primary_sms",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"🔔 Multi-Channel: {'✅ ON' if multi_channel else '❌ OFF'}",
                        callback_data="toggle_multi_channel",
                    )
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="contact_menu")],
            ]

            await safe_edit_message_text(
                query,
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

            return NOTIFICATION_PREFERENCES

        except Exception as e:
            logger.error(f"Error in notification preferences menu: {e}")
            return ConversationHandler.END
        finally:
            if "session" in locals():
                session.close()

    async def update_notification_preference(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Update notification preference setting"""
        session = SessionLocal()
        try:
            query = update.callback_query
            if not query:
                # Graceful recovery - return to main menu
                from handlers.start import show_main_menu
                await show_main_menu(update, context)
                return ConversationHandler.END
            # IMMEDIATE FEEDBACK: Contact action
            await safe_answer_callback_query(query, "📞 Contact action")

            user_id = (
                getattr(update.effective_user, "id", 0) if update.effective_user else 0
            )
            from utils.repository import UserRepository
            user = UserRepository.get_user_by_telegram_id(session, user_id)

            if not user:
                await safe_edit_message_text(
                    query,
                    "❌ User not found.",
                    reply_markup=main_menu_keyboard(),  # Simple fallback for contact management
                )
                # Graceful recovery - return to main menu
                from handlers.start import show_main_menu
                await show_main_menu(update, context)
                return ConversationHandler.END

            preferences = getattr(user, "notification_preferences", {}) or {}

            if query.data and query.data.startswith("set_primary_"):
                # Update primary channel via SQL to avoid Column assignment issues
                channel = query.data.replace("set_primary_", "")
                from sqlalchemy import update as sql_update

                session.execute(
                    sql_update(User)
                    .where(User.id == user.id)
                    .values(primary_notification_channel=channel)
                )

                await safe_answer_callback_query(query, f"✅ Primary channel set to {channel.title()}")

            elif query.data == "toggle_multi_channel":
                # Toggle multi-channel notifications via SQL to avoid Column assignment issues
                current = preferences.get("multi_channel_notifications", True)
                preferences["multi_channel_notifications"] = not current
                from sqlalchemy import update as sql_update

                session.execute(
                    sql_update(User)
                    .where(User.id == user.id)
                    .values(notification_preferences=preferences)
                )

                status = "enabled" if not current else "disabled"
                await safe_answer_callback_query(query, f"✅ Multi-channel notifications {status}")

            session.commit()

            # Refresh the menu
            return await self.notification_preferences_menu(update, context)

        except Exception as e:
            logger.error(f"Error updating notification preference: {e}")
            if "session" in locals():
                session.rollback()
            return ConversationHandler.END
        finally:
            if "session" in locals():
                session.close()

    async def view_all_contacts(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Show detailed view of all user contacts"""
        session = SessionLocal()
        try:
            query = update.callback_query
            if not query:
                # Graceful recovery - return to main menu
                from handlers.start import show_main_menu
                await show_main_menu(update, context)
                return ConversationHandler.END
            # IMMEDIATE FEEDBACK: Contact action
            await safe_answer_callback_query(query, "📞 Contact action")

            user_id = (
                getattr(update.effective_user, "id", 0) if update.effective_user else 0
            )
            from utils.repository import UserRepository
            user = UserRepository.get_user_by_telegram_id(session, user_id)

            if not user:
                await safe_edit_message_text(
                    query,
                    "❌ User not found.",
                    reply_markup=main_menu_keyboard(),  # Simple fallback for contact management
                )
                # Graceful recovery - return to main menu
                from handlers.start import show_main_menu
                await show_main_menu(update, context)
                return ConversationHandler.END

            # Get all contacts
            contacts = contact_detection_service.get_all_linked_contacts(
                getattr(user, "id", 0)
            )

            text = "📋 All Contact Methods\n\n"

            if not contacts:
                text += "No verified contact methods found.\n\n"
            else:
                for contact in contacts:
                    primary_indicator = "🌟 " if contact.is_primary else ""
                    verified_indicator = "✅" if contact.is_verified else "⏳"

                    # Mask sensitive information
                    masked_value = self._mask_contact_value(
                        contact.contact_value, contact.contact_type
                    )

                    text += f"{primary_indicator}{contact.contact_type.title()}: {masked_value} {verified_indicator}\n"
                    text += f"   📊 Avg Response: {contact.avg_response_time:.1f}h\n"

                    if contact.last_used:
                        text += f"   ⏰ Last Used: {contact.last_used.strftime('%Y-%m-%d %H:%M')}\n"

                    text += f"   📬 Notifications: {'✅' if contact.notification_enabled else '❌'}\n\n"

            keyboard = [
                [InlineKeyboardButton("➕ Add Contact", callback_data="add_contact")],
                [
                    InlineKeyboardButton(
                        "🔔 Notification Settings",
                        callback_data="notification_settings",
                    )
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="contact_menu")],
            ]

            await safe_edit_message_text(
                query,
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

            return CONTACT_MENU

        except Exception as e:
            logger.error(f"Error viewing all contacts: {e}")
            return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Cancel contact management conversation"""
        try:
            query = update.callback_query
            if query:
                # IMMEDIATE FEEDBACK: Contact action
                await safe_answer_callback_query(query, "📞 Contact action")
                await safe_edit_message_text(
                    query,
                    "✅ Contact management cancelled.",
                    reply_markup=main_menu_keyboard(),  # Simple fallback for contact management
                )
            else:
                if update.message:
                    await update.message.reply_text(
                        "✅ Contact management cancelled.",
                        reply_markup=main_menu_keyboard(),  # Simple fallback for contact management
                    )

            # Clear context data
            if context.user_data is not None:
                context.user_data.pop("adding_contact_type", None)
                context.user_data.pop("verifying_contact_id", None)

            return ConversationHandler.END

        except Exception as e:
            logger.error(f"Error cancelling contact management: {e}")
            return ConversationHandler.END

    def _validate_contact_value(self, value: str, contact_type: str) -> Dict[str, Any]:
        """SECURITY: Enhanced contact value validation with sanitization"""
        # SECURITY: Sanitize input first
        from utils.input_validation import sanitize_text_input, validate_safe_user_input

        try:
            value = sanitize_text_input(value)
            value = validate_safe_user_input(
                value, max_length=100, allow_special_chars=True
            )
        except Exception as e:
            return {"valid": False, "error": str(e)}

        if contact_type == "email":
            # Enhanced email validation with security checks
            email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
            if re.match(email_pattern, value) and len(value) <= 254:  # RFC 5321 limit
                return {"valid": True, "normalized": value.lower().strip()}
            else:
                return {
                    "valid": False,
                    "error": "Invalid email format. Please enter a valid email address.",
                }

        elif contact_type == "phone":
            try:
                # Parse phone number
                parsed = phonenumbers.parse(value, None)
                if phonenumbers.is_valid_number(parsed):
                    normalized = phonenumbers.format_number(
                        parsed, phonenumbers.PhoneNumberFormat.E164
                    )
                    return {"valid": True, "normalized": normalized}
                else:
                    return {
                        "valid": False,
                        "error": "Invalid phone number. Please include country code (e.g., {Config.EXAMPLE_PHONE_NUMBER}).",
                    }
            except Exception as e:
                logger.debug(f"Phone number parsing failed: {e}")
                return {
                    "valid": False,
                    "error": "Invalid phone number format. Please include country code (e.g., {Config.EXAMPLE_PHONE_NUMBER}).",
                }

        return {"valid": False, "error": "Unknown contact type."}

    def _contact_already_exists(
        self, user_id: int, contact_type: str, contact_value: str
    ) -> bool:
        """Check if contact already exists for user"""
        session = SessionLocal()
        try:
            # Check primary contacts
            user = session.query(User).filter(User.id == user_id).first()
            if user:
                user_email = getattr(user, "email", None)
                user_phone = getattr(user, "phone_number", None)
                if (
                    contact_type == "email"
                    and user_email
                    and str(user_email).lower() == contact_value.lower()
                ):
                    return True
                if (
                    contact_type == "phone"
                    and user_phone
                    and str(user_phone) == contact_value
                ):
                    return True

            # Check additional contacts
            existing = (
                session.query(UserContact)
                .filter(
                    UserContact.user_id == user_id,
                    UserContact.contact_type == contact_type,
                    UserContact.contact_value == contact_value,
                    UserContact.is_active,
                )
                .first()
            )

            return existing is not None
        finally:
            session.close()

    def _generate_verification_code(self) -> str:
        """Generate 6-digit verification code"""
        import secrets

        return ''.join(secrets.choice('0123456789') for _ in range(6))

    async def _send_verification_code(
        self, contact_type: str, contact_value: str, code: str
    ) -> bool:
        """Send verification code to contact"""
        try:
            if contact_type == "email":
                subject = "LockBay - Verify Your Email"
                html_content = f"""
                <html>
                <body style="font-family: Arial, sans-serif;">
                    <h2>Verify Your Email</h2>
                    <p>Your verification code is:</p>
                    <h1 style="background: #f0f0f0; padding: 20px; text-align: center; letter-spacing: 5px;">{code}</h1>
                    <p>This code will expire in {Config.OTP_EXPIRY_MINUTES} minutes.</p>
                    <p>If you didn't request this verification, please ignore this email.</p>
                </body>
                </html>
                """
                from services.notification_service import notification_service

                notification_service = notification_service()
                result = await notification_service.send_notification(
                    user_id=0,  # Use 0 instead of None for direct email
                    notification_type="email",
                    message=html_content,
                    subject=subject,
                    email_override=contact_value
                )
                # Convert dict result to boolean
                return isinstance(result, dict) and result.get('success', False)

            elif contact_type == "phone":
                # SMS verification disabled
                logger.info("Phone verification via SMS has been disabled")
                return False

            return False

        except Exception as e:
            logger.error(f"Error sending verification code to {contact_type}: {e}")
            return False

    def _mask_contact_value(self, value: str, contact_type: str) -> str:
        """Mask sensitive parts of contact values"""
        if contact_type == "email":
            parts = value.split("@")
            if len(parts) == 2:
                username, domain = parts
                if len(username) > 3:
                    masked_username = username[:2] + "*" * (len(username) - 2)
                    return f"{masked_username}@{domain}"
            return value[:3] + "*" * (len(value) - 3)

        elif contact_type == "phone":
            if len(value) > 8:
                return value[:4] + "*" * (len(value) - 8) + value[-4:]
            return value[:2] + "*" * (len(value) - 2)

        return value

# Create handler instance
contact_handler = ContactManagementHandler()

# NOTE: ConversationHandler removed - was unused and not registered in main.py
# Individual contact management handlers can be registered directly if needed
