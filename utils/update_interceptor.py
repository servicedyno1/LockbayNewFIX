"""
Update Interceptor Middleware for Comprehensive Telegram Interaction Logging
Captures all Telegram updates before other handlers process them
"""

import logging
import time
import uuid
from typing import Optional, Dict, Any, cast
from telegram import Update
from telegram.ext import ContextTypes, ApplicationHandlerStop

from utils.comprehensive_audit_logger import (
    audit_user_interaction, 
    TraceContext, 
    AuditLevel,
    PIISafeDataExtractor
)
from config import Config

# Import unified activity monitor for real-time tracking
unified_monitor = None

def set_unified_monitor(monitor):
    """Set the unified monitor instance"""
    global unified_monitor
    unified_monitor = monitor

logger = logging.getLogger(__name__)


class UpdateInterceptor:
    """
    High-priority middleware to intercept and log all Telegram interactions
    before they are processed by other handlers
    """
    
    def __init__(self):
        self.data_extractor = PIISafeDataExtractor()
        self.interaction_counter = 0
    
    async def intercept_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Main interceptor function that captures all Telegram updates
        
        Args:
            update: Telegram Update object
            context: Bot context
        """
        start_time = time.time()
        
        try:
            # Generate trace ID for this interaction
            trace_id = str(uuid.uuid4())
            f"req_{int(time.time())}_{self.interaction_counter}"
            self.interaction_counter += 1
            
            # Set trace context
            TraceContext.set_trace_id(trace_id)
            
            # Extract user and chat information
            user_id = None
            chat_id = None
            message_id = None
            is_admin = False
            
            if update.effective_user:
                user_id = update.effective_user.id
                is_admin = user_id in Config.ADMIN_IDS
                TraceContext.set_user_context(user_id)
            
            if update.effective_chat:
                chat_id = update.effective_chat.id
                if user_id is not None:
                    TraceContext.set_user_context(user_id, chat_id)
            
            if update.effective_message:
                message_id = update.effective_message.message_id
            
            # MAINTENANCE MODE CHECK - Block non-admin users during maintenance
            if Config.get_maintenance_mode():
                # Allow admin access during maintenance
                from utils.admin_security import is_admin_secure
                if not is_admin_secure(user_id) if user_id else False:
                    # Block non-admin users with friendly message
                    await self._send_maintenance_message(update, context)
                    logger.info(f"🔒 MAINTENANCE MODE: Blocked user {user_id}")
                    # Stop ALL further processing by raising ApplicationHandlerStop
                    raise ApplicationHandlerStop
            
            # Generate session ID (could be enhanced with actual session management)
            session_id = f"session_{user_id}_{int(time.time() // 3600)}"  # Hour-based session
            TraceContext.set_session_id(session_id)
            
            # Determine interaction type and extract metadata
            interaction_data = self._analyze_update(update)
            
            # Log the interaction
            audit_user_interaction(
                action=f"telegram_{interaction_data['type']}",
                update=update,
                result="intercepted",
                level=AuditLevel.INFO,
                user_id=user_id,
                is_admin=is_admin,
                chat_id=chat_id,
                message_id=message_id,
                latency_ms=(time.time() - start_time) * 1000,
                **interaction_data['metadata']
            )
            
            # Track user interaction in unified monitor for real-time dashboard
            if user_id and unified_monitor:
                try:
                    username = getattr(update.effective_user, 'username', None) or f"user_{user_id}"
                    action = f"{interaction_data['type']}: {interaction_data.get('metadata', {}).get('command_name', 'interaction')}"
                    unified_monitor.track_user_interaction(
                        user_id=user_id,
                        username=username, 
                        action=action,
                        details=interaction_data['metadata'],
                        trace_id=trace_id
                    )
                except Exception as e:
                    logger.warning(f"Failed to track user in unified monitor: {e}")

            # Log detailed interaction metadata
            logger.info(
                f"📡 INTERCEPTED: {interaction_data['type']} from user {user_id} "
                f"(trace: {trace_id[:8]}, session: {session_id[-8:]})"
            )
            
        except ApplicationHandlerStop:
            # Re-raise to actually stop handler processing
            raise
        except Exception as e:
            # Never fail the main processing due to interceptor issues
            logger.error(f"❌ Update interceptor failed: {e}", exc_info=True)
    
    async def _send_maintenance_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send maintenance mode notification to users"""
        try:
            # Get custom maintenance message from database
            maintenance_message = Config.get_maintenance_message()
            
            # Get duration and remaining time
            duration_minutes = Config.get_maintenance_duration()
            remaining_seconds = Config.get_maintenance_time_remaining()
            
            # Build time information
            time_info = ""
            if duration_minutes and remaining_seconds is not None:
                # Format total duration
                if duration_minutes < 60:
                    total_duration = f"{duration_minutes} minutes"
                else:
                    hours = duration_minutes // 60
                    mins = duration_minutes % 60
                    total_duration = f"{hours}h {mins}m" if mins > 0 else f"{hours} hour{'s' if hours > 1 else ''}"
                
                # Format remaining time
                if remaining_seconds > 0:
                    remaining_mins = remaining_seconds // 60
                    remaining_secs = remaining_seconds % 60
                    remaining_time = f"{remaining_mins} min {remaining_secs} sec"
                    time_info = f"\n⏰ **Expected downtime:** {total_duration}\n⏳ **Time remaining:** {remaining_time}"
                else:
                    time_info = f"\n⏰ **Expected downtime:** {total_duration}\n✅ **Maintenance should be completing soon!**"
            elif duration_minutes:
                # Duration specified but no start time (shouldn't happen, but handle gracefully)
                if duration_minutes < 60:
                    total_duration = f"{duration_minutes} minutes"
                else:
                    hours = duration_minutes // 60
                    total_duration = f"{hours} hour{'s' if hours > 1 else ''}"
                time_info = f"\n⏰ **Expected downtime:** {total_duration}"
            else:
                # No duration specified
                time_info = "\n⏰ **Status:** Temporary maintenance"
            
            full_message = f"""
🔧 **System Maintenance in Progress**

{maintenance_message}{time_info}

🔔 **Updates:** Service will resume shortly

Thank you for your patience! 🙏

_Your funds and data are safe and secure._
            """.strip()
            
            # Send message based on update type
            if update.message:
                await update.message.reply_text(
                    full_message,
                    parse_mode='Markdown'
                )
            elif update.callback_query:
                # Create a shorter alert message for callback queries
                alert_message = "⚙️ System maintenance in progress."
                if remaining_seconds and remaining_seconds > 0:
                    remaining_mins = remaining_seconds // 60
                    alert_message += f" ~{remaining_mins} min remaining."
                else:
                    alert_message += " Please try again later."
                
                await update.callback_query.answer(
                    alert_message,
                    show_alert=True
                )
        except Exception as e:
            logger.error(f"Failed to send maintenance message: {e}")
    
    def _analyze_update(self, update: Update) -> Dict[str, Any]:
        """
        Analyze update and extract safe metadata
        
        Args:
            update: Telegram Update object
            
        Returns:
            Dictionary with interaction type and safe metadata
        """
        interaction_data: Dict[str, Any] = {
            'type': 'unknown',
            'metadata': {}
        }
        
        try:
            # Command messages
            if update.message and update.message.text and update.message.text.startswith('/'):
                interaction_data['type'] = 'command'
                command = update.message.text.split()[0]
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    'command_name': command,
                    'text_length': len(update.message.text),
                    'has_parameters': len(update.message.text.split()) > 1,
                    'is_bot_command': update.message.text.startswith('/'),
                    'chat_type': update.effective_chat.type if update.effective_chat else None
                })
            
            # Callback query (button clicks)
            elif update.callback_query:
                interaction_data['type'] = 'callback_query'
                callback_data = update.callback_query.data or ""
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    'callback_data_type': self._extract_callback_type(callback_data),
                    'callback_data_length': len(callback_data),
                    'has_message': update.callback_query.message is not None,
                    'chat_type': update.effective_chat.type if update.effective_chat else None
                })
            
            # Text messages
            elif update.message and update.message.text:
                interaction_data['type'] = 'text_message'
                text = update.message.text
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    'text_length': len(text),
                    'contains_email': self._contains_email(text),
                    'contains_phone': self._contains_phone(text),
                    'contains_crypto_address': self._contains_crypto_address(text),
                    'contains_url': 'http' in text.lower(),
                    'is_reply': update.message.reply_to_message is not None,
                    'chat_type': update.effective_chat.type if update.effective_chat else None
                })
            
            # File uploads
            elif update.message and (update.message.document or update.message.photo or 
                                   update.message.audio or update.message.video or 
                                   update.message.voice or update.message.sticker):
                interaction_data['type'] = 'file_upload'
                file_metadata = self._extract_file_metadata(update.message)
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    **file_metadata,
                    'chat_type': update.effective_chat.type if update.effective_chat else None
                })
            
            # Contact sharing
            elif update.message and update.message.contact:
                interaction_data['type'] = 'contact_share'
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    'has_phone_number': update.message.contact.phone_number is not None,
                    'has_first_name': update.message.contact.first_name is not None,
                    'is_self_contact': update.message.contact.user_id == update.effective_user.id if update.effective_user else False,
                    'chat_type': update.effective_chat.type if update.effective_chat else None
                })
            
            # Location sharing
            elif update.message and update.message.location:
                interaction_data['type'] = 'location_share'
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    'has_live_period': update.message.location.live_period is not None,
                    'chat_type': update.effective_chat.type if update.effective_chat else None
                })
            
            # Inline queries
            elif update.inline_query:
                interaction_data['type'] = 'inline_query'
                query = update.inline_query.query or ""
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    'query_length': len(query),
                    'has_offset': update.inline_query.offset != "",
                    'query_empty': len(query) == 0
                })
            
            # Chosen inline result
            elif update.chosen_inline_result:
                interaction_data['type'] = 'chosen_inline_result'
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    'result_id': update.chosen_inline_result.result_id[:20] if update.chosen_inline_result.result_id else None,
                    'has_query': update.chosen_inline_result.query is not None
                })
            
            # Poll updates
            elif update.poll:
                interaction_data['type'] = 'poll_update'
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    'poll_id': update.poll.id[:20] if update.poll.id else None,
                    'is_closed': update.poll.is_closed,
                    'total_voter_count': update.poll.total_voter_count,
                    'option_count': len(update.poll.options)
                })
            
            # Poll answer
            elif update.poll_answer:
                interaction_data['type'] = 'poll_answer'
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    'poll_id': update.poll_answer.poll_id[:20] if update.poll_answer.poll_id else None,
                    'option_count': len(update.poll_answer.option_ids)
                })
            
            # Pre-checkout query
            elif update.pre_checkout_query:
                interaction_data['type'] = 'pre_checkout_query'
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    'currency': update.pre_checkout_query.currency,
                    'total_amount': update.pre_checkout_query.total_amount,
                    'has_order_info': update.pre_checkout_query.order_info is not None
                })
            
            # Shipping query
            elif update.shipping_query:
                interaction_data['type'] = 'shipping_query'
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    'has_shipping_address': update.shipping_query.shipping_address is not None
                })
            
            # Chat member updates
            elif update.chat_member:
                interaction_data['type'] = 'chat_member_update'
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    'old_status': update.chat_member.old_chat_member.status,
                    'new_status': update.chat_member.new_chat_member.status,
                    'chat_type': update.effective_chat.type if update.effective_chat else None
                })
            
            # My chat member updates
            elif update.my_chat_member:
                interaction_data['type'] = 'my_chat_member_update'
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    'old_status': update.my_chat_member.old_chat_member.status,
                    'new_status': update.my_chat_member.new_chat_member.status,
                    'chat_type': update.effective_chat.type if update.effective_chat else None
                })
            
            # Chat join request
            elif update.chat_join_request:
                interaction_data['type'] = 'chat_join_request'
                interaction_data['metadata'] = cast(Dict[str, Any], {
                    'has_bio': update.chat_join_request.bio is not None,
                    'chat_type': update.effective_chat.type if update.effective_chat else None
                })
            
            # Add timestamp to all metadata
            interaction_data['metadata']['timestamp'] = time.time()
            interaction_data['metadata']['update_id'] = update.update_id
            
        except Exception as e:
            logger.warning(f"Failed to analyze update: {e}")
            interaction_data['metadata']['analysis_error'] = str(e)
        
        return interaction_data
    
    def _extract_callback_type(self, callback_data: str) -> str:
        """Extract safe callback type from callback data"""
        if not callback_data:
            return "empty"
        
        # Extract first part of callback data as type
        if ':' in callback_data:
            return callback_data.split(':')[0]
        return callback_data[:20] if len(callback_data) > 20 else callback_data
    
    def _contains_email(self, text: str) -> bool:
        """Check if text contains email pattern"""
        return '@' in text and '.' in text.split('@')[-1] if '@' in text else False
    
    def _contains_phone(self, text: str) -> bool:
        """Check if text contains phone pattern"""
        return text.startswith('+') and any(c.isdigit() for c in text)
    
    def _contains_crypto_address(self, text: str) -> bool:
        """Check if text contains crypto address pattern"""
        if len(text) < 20:
            return False
        
        # Bitcoin addresses
        if text.startswith(('1', '3', 'bc1')):
            return True
        
        # Ethereum addresses
        if text.startswith('0x') and len(text) == 42:
            return True
        
        # Other common crypto address patterns
        common_prefixes = ['ltc1', 'D', 'L', 'M']
        for prefix in common_prefixes:
            if text.startswith(prefix) and len(text) > 25:
                return True
        
        return False
    
    def _extract_file_metadata(self, message) -> Dict[str, Any]:
        """Extract safe metadata from file uploads"""
        metadata = {}
        
        if message.document:
            metadata['file_type'] = 'document'
            metadata['file_size'] = message.document.file_size
            metadata['mime_type'] = message.document.mime_type
            metadata['has_filename'] = message.document.file_name is not None
        elif message.photo:
            metadata['file_type'] = 'photo'
            # Get largest photo size
            largest_photo = max(message.photo, key=lambda x: x.file_size or 0)
            metadata['file_size'] = largest_photo.file_size
            metadata['width'] = largest_photo.width
            metadata['height'] = largest_photo.height
        elif message.audio:
            metadata['file_type'] = 'audio'
            metadata['file_size'] = message.audio.file_size
            metadata['duration'] = message.audio.duration
            metadata['has_performer'] = message.audio.performer is not None
            metadata['has_title'] = message.audio.title is not None
        elif message.video:
            metadata['file_type'] = 'video'
            metadata['file_size'] = message.video.file_size
            metadata['duration'] = message.video.duration
            metadata['width'] = message.video.width
            metadata['height'] = message.video.height
        elif message.voice:
            metadata['file_type'] = 'voice'
            metadata['file_size'] = message.voice.file_size
            metadata['duration'] = message.voice.duration
        elif message.sticker:
            metadata['file_type'] = 'sticker'
            metadata['file_size'] = message.sticker.file_size
            metadata['width'] = message.sticker.width
            metadata['height'] = message.sticker.height
            metadata['is_animated'] = message.sticker.is_animated
            metadata['is_video'] = message.sticker.is_video
        
        return metadata


# Global instance
update_interceptor = UpdateInterceptor()


def register_update_interceptor(application) -> None:
    """
    Register the update interceptor with the highest priority
    
    Args:
        application: Telegram Application instance
    """
    try:
        # Use a very high priority (lowest group number) to ensure we capture everything first
        # Group -100 ensures this runs before all other handlers
        INTERCEPTOR_GROUP = -100
        
        # Register a generic handler that captures ALL updates
        from telegram.ext import TypeHandler
        
        # Create handler that captures all Update objects
        interceptor_handler = TypeHandler(Update, update_interceptor.intercept_update)
        
        # Add with highest priority
        application.add_handler(interceptor_handler, group=INTERCEPTOR_GROUP)
        
        logger.info(f"✅ Update Interceptor registered with priority group {INTERCEPTOR_GROUP}")
        
    except Exception as e:
        logger.error(f"❌ Failed to register Update Interceptor: {e}", exc_info=True)


def get_current_trace_id() -> Optional[str]:
    """Get current trace ID for correlation"""
    return TraceContext.get_trace_id()


def get_current_session_id() -> Optional[str]:
    """Get current session ID for correlation"""
    return TraceContext.get_session_id()