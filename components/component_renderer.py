"""
Component Renderer - Renders Scene Engine components

Generates Telegram UI elements (keyboards, messages) for scene components.
Provides consistent styling and layout across all components.
"""

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from services.scene_engine import SceneState, ComponentConfig, SceneStep, ComponentType
from utils.message_utils import send_unified_message
from .ui_standards import UIStandards

logger = logging.getLogger(__name__)

class ComponentRenderer:
    """Renders Scene Engine components to Telegram UI with standardized patterns"""
    
    def __init__(self):
        self.component_renderers = {
            ComponentType.AMOUNT_INPUT: self._render_amount_input,
            ComponentType.ADDRESS_SELECTOR: self._render_address_selector,
            ComponentType.BANK_SELECTOR: self._render_bank_selector,
            ComponentType.CONFIRMATION: self._render_confirmation,
            ComponentType.STATUS_DISPLAY: self._render_status_display,
            ComponentType.CUSTOM_KEYBOARD: self._render_custom_keyboard,
            ComponentType.TEXT_INPUT: self._render_text_input,
            ComponentType.SELECTION_MENU: self._render_selection_menu,
        }
    
    async def render_component(
        self,
        user_id: int,
        scene_state: SceneState,
        component_config: ComponentConfig,
        step: SceneStep
    ) -> bool:
        """Render a component for a user with standardized UI patterns"""
        try:
            renderer = self.component_renderers.get(component_config.component_type)
            if not renderer:
                logger.error(f"No renderer for component type: {component_config.component_type}")
                return False
            
            await renderer(user_id, scene_state, component_config, step)
            return True
            
        except Exception as e:
            logger.error(f"Component rendering error: {e}")
            await self._render_error_message(user_id, 'processing_failed', str(e), step)
            return False
    
    async def _render_amount_input(
        self, 
        user_id: int, 
        scene_state: SceneState, 
        component_config: ComponentConfig, 
        step: SceneStep
    ) -> None:
        """Render standardized amount input component"""
        config = component_config.config
        currency = config.get('currency', 'USD')
        min_amount = config.get('min_amount', 0.01)
        max_amount = config.get('max_amount', 10000)
        
        # Standardized header
        text = UIStandards.MESSAGE_TEMPLATES['header'].format(
            icon=UIStandards.ICONS['amount'],
            title=step.title,
            description=step.description
        )
        
        # Currency and range information
        text += UIStandards.MESSAGE_TEMPLATES['section_header'].format(
            icon=UIStandards.ICONS['currency'],
            section_name="Transaction Details"
        )
        text += f"Currency: {currency}\n"
        text += f"Range: {UIStandards.format_currency(min_amount, currency)} - {UIStandards.format_currency(max_amount, currency)}\n\n"
        
        # Standardized input prompt
        text += UIStandards.MESSAGE_TEMPLATES['input_prompt'].format(
            icon=UIStandards.ICONS['input'],
            input_type="the amount"
        )
        
        # Add timeout warning
        text += "\n" + UIStandards.get_timeout_message('amount_input', 
                                                   UIStandards.TIMEOUT_SETTINGS['amount_input']['seconds'])
        
        # Create quick amount buttons if configured
        keyboard = []
        if config.get('quick_amounts'):
            amounts = config['quick_amounts']
            row = []
            for i, amount in enumerate(amounts):
                formatted_amount = UIStandards.format_currency(amount, currency)
                row.append(InlineKeyboardButton(
                    formatted_amount, 
                    callback_data=f"amount_{amount}"
                ))
                if len(row) == 2 or i == len(amounts) - 1:
                    keyboard.append(row)
                    row = []
        
        # Add standardized navigation
        nav_buttons = ['back'] if step.can_go_back else []
        if nav_buttons:
            keyboard.extend(UIStandards.build_keyboard_row(nav_buttons, max_per_row=1))
        
        await send_unified_message(
            user_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )
    
    async def _render_address_selector(
        self,
        user_id: int,
        scene_state: SceneState,
        component_config: ComponentConfig,
        step: SceneStep
    ) -> None:
        """Render standardized address selector component"""
        config = component_config.config
        crypto = config.get('crypto', 'BTC')
        network = config.get('network', 'mainnet')
        
        # Standardized header
        text = UIStandards.MESSAGE_TEMPLATES['header'].format(
            icon=UIStandards.ICONS['address'],
            title=step.title,
            description=step.description
        )
        
        # Crypto and network information
        text += UIStandards.MESSAGE_TEMPLATES['section_header'].format(
            icon=UIStandards.ICONS['crypto'],
            section_name="Network Details"
        )
        text += f"Cryptocurrency: {crypto}\n"
        text += f"Network: {network}\n\n"
        
        # Check for saved addresses
        saved_addresses = scene_state.data.get('saved_addresses', [])
        
        keyboard = []
        if saved_addresses:
            text += UIStandards.MESSAGE_TEMPLATES['section_header'].format(
                icon=UIStandards.ICONS['save'],
                section_name="Saved Addresses"
            )
            for i, addr in enumerate(saved_addresses[:3]):  # Show max 3
                formatted_addr = UIStandards.format_address(addr['address'], crypto)
                text += f"   {i+1}. {addr['label']}: {formatted_addr}\n"
                keyboard.append([InlineKeyboardButton(
                    f"{UIStandards.ICONS['select']} Use {addr['label']}", 
                    callback_data=f"address_{i}"
                )])
            text += "\n"
        
        # Input prompt
        if saved_addresses:
            text += UIStandards.MESSAGE_TEMPLATES['selection_prompt'].format(
                icon=UIStandards.ICONS['input']
            )
            keyboard.append([InlineKeyboardButton(
                f"{UIStandards.ICONS['add']} Add New Address", 
                callback_data="add_new_address"
            )])
        else:
            text += UIStandards.MESSAGE_TEMPLATES['input_prompt'].format(
                icon=UIStandards.ICONS['input'],
                input_type="your withdrawal address"
            )
        
        # Add timeout warning
        text += "\n" + UIStandards.get_timeout_message('address_input', 
                                                   UIStandards.TIMEOUT_SETTINGS['address_input']['seconds'])
        
        # Add standardized navigation
        nav_buttons = ['back'] if step.can_go_back else []
        if nav_buttons:
            keyboard.extend(UIStandards.build_keyboard_row(nav_buttons, max_per_row=1))
        
        await send_unified_message(
            user_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )
    
    async def _render_bank_selector(
        self,
        user_id: int,
        scene_state: SceneState,
        component_config: ComponentConfig,
        step: SceneStep
    ) -> None:
        """Render standardized bank selector component"""
        # Standardized header
        text = UIStandards.MESSAGE_TEMPLATES['header'].format(
            icon=UIStandards.ICONS['bank'],
            title=step.title,
            description=step.description
        )
        
        # Check for saved banks
        saved_banks = scene_state.data.get('saved_banks', [])
        
        keyboard = []
        if saved_banks:
            text += UIStandards.MESSAGE_TEMPLATES['section_header'].format(
                icon=UIStandards.ICONS['save'],
                section_name="Saved Bank Accounts"
            )
            for i, bank in enumerate(saved_banks[:3]):  # Show max 3
                text += f"   {i+1}. {bank['bank_name']} - {bank['account_name']}\n"
                keyboard.append([InlineKeyboardButton(
                    f"{UIStandards.ICONS['select']} Use {bank['bank_name']}", 
                    callback_data=f"bank_{i}"
                )])
            text += "\n"
        
        # Selection prompt
        text += UIStandards.MESSAGE_TEMPLATES['selection_prompt'].format(
            icon=UIStandards.ICONS['input']
        )
        
        # Add new bank option
        keyboard.append([InlineKeyboardButton(
            f"{UIStandards.ICONS['add']} Add New Bank Account", 
            callback_data="add_new_bank"
        )])
        
        # Add timeout warning
        text += "\n" + UIStandards.get_timeout_message('bank_selection', 
                                                   UIStandards.TIMEOUT_SETTINGS['bank_selection']['seconds'])
        
        # Add standardized navigation
        nav_buttons = ['back'] if step.can_go_back else []
        if nav_buttons:
            keyboard.extend(UIStandards.build_keyboard_row(nav_buttons, max_per_row=1))
        
        await send_unified_message(
            user_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )
    
    async def _render_confirmation(
        self,
        user_id: int,
        scene_state: SceneState,
        component_config: ComponentConfig,
        step: SceneStep
    ) -> None:
        """Render standardized confirmation component"""
        config = component_config.config
        data = scene_state.data
        
        # Standardized header
        text = UIStandards.MESSAGE_TEMPLATES['header'].format(
            icon=UIStandards.ICONS['confirm'],
            title=step.title,
            description=step.description
        )
        
        # Build confirmation summary
        if config.get('show_summary'):
            text += UIStandards.MESSAGE_TEMPLATES['section_header'].format(
                icon=UIStandards.ICONS['summary'],
                section_name="Transaction Summary"
            )
            
            # Amount and currency
            if 'amount' in data and 'currency' in data:
                formatted_amount = UIStandards.format_currency(float(data['amount']), data['currency'])
                text += f"{UIStandards.ICONS['amount']} Amount: {formatted_amount}\n"
            elif 'amount' in data:
                text += f"{UIStandards.ICONS['amount']} Amount: {data['amount']}\n"
            
            # Destination details
            if 'destination' in data:
                dest = data['destination']
                if isinstance(dest, dict):
                    if 'bank_name' in dest:
                        text += f"{UIStandards.ICONS['bank']} Bank: {dest['bank_name']}\n"
                        text += f"👤 Account: {dest['account_name']}\n"
                    elif 'address' in dest:
                        formatted_addr = UIStandards.format_address(dest['address'])
                        text += f"{UIStandards.ICONS['address']} Address: {formatted_addr}\n"
                else:
                    text += f"📍 Destination: {dest}\n"
            
            # Fee and total
            if 'fee' in data:
                currency = data.get('currency', 'USD')
                formatted_fee = UIStandards.format_currency(float(data['fee']), currency)
                text += f"{UIStandards.ICONS['fee']} Fee: {formatted_fee}\n"
            if 'total' in data:
                currency = data.get('currency', 'USD')
                formatted_total = UIStandards.format_currency(float(data['total']), currency)
                text += f"{UIStandards.ICONS['total']} Total: {formatted_total}\n"
            text += "\n"
        
        # Confirmation prompt
        text += UIStandards.MESSAGE_TEMPLATES['confirmation_prompt'].format(
            icon=UIStandards.ICONS['verify']
        )
        
        # Add timeout warning
        text += "\n" + UIStandards.get_timeout_message('confirmation', 
                                                   UIStandards.TIMEOUT_SETTINGS['confirmation']['seconds'])
        
        # Standardized confirmation buttons
        keyboard = UIStandards.build_keyboard_row(['confirm', 'deny'], max_per_row=2)
        
        # Add navigation
        nav_buttons = ['back'] if step.can_go_back else []
        if nav_buttons:
            keyboard.extend(UIStandards.build_keyboard_row(nav_buttons, max_per_row=1))
        
        await send_unified_message(
            user_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )
    
    async def _render_status_display(
        self,
        user_id: int,
        scene_state: SceneState,
        component_config: ComponentConfig,
        step: SceneStep
    ) -> None:
        """Render standardized status display component"""
        data = scene_state.data
        
        status = data.get('status', 'processing')
        
        # Get standardized status icon
        icon = UIStandards.ICONS.get(status, UIStandards.ICONS['details'])
        
        # Standardized header
        text = UIStandards.MESSAGE_TEMPLATES['header'].format(
            icon=icon,
            title=step.title,
            description=step.description
        )
        
        # Transaction details section
        if any(key in data for key in ['transaction_id', 'estimated_time', 'amount', 'currency']):
            text += UIStandards.MESSAGE_TEMPLATES['section_header'].format(
                icon=UIStandards.ICONS['details'],
                section_name="Transaction Details"
            )
            
            if 'transaction_id' in data:
                text += f"{UIStandards.ICONS['id']} Transaction ID: {data['transaction_id']}\n"
            if 'amount' in data and 'currency' in data:
                formatted_amount = UIStandards.format_currency(float(data['amount']), data['currency'])
                text += f"{UIStandards.ICONS['amount']} Amount: {formatted_amount}\n"
            if 'estimated_time' in data:
                text += f"{UIStandards.ICONS['time']} Estimated completion: {data['estimated_time']}\n"
            text += "\n"
        
        # Status-specific messaging
        if status == 'completed':
            text += UIStandards.MESSAGE_TEMPLATES['success_message'].format(
                icon=UIStandards.ICONS['success'],
                message="Your transaction has been completed successfully!"
            )
        elif status == 'failed':
            error_details = data.get('error_message', 'Transaction processing failed')
            text += UIStandards.MESSAGE_TEMPLATES['error_message'].format(
                icon=UIStandards.ICONS['failed'],
                error_type="Transaction Failed",
                message=error_details
            )
        elif status == 'processing':
            text += UIStandards.MESSAGE_TEMPLATES['processing_message'].format(
                icon=UIStandards.ICONS['processing'],
                message="Your transaction is being processed..."
            )
        
        # Status-specific action buttons
        action_buttons = []
        if status == 'completed':
            action_buttons.append('new_transaction')
        elif status == 'failed':
            action_buttons.append('retry')
        
        # Build keyboard
        keyboard = []
        if action_buttons:
            keyboard.extend(UIStandards.build_keyboard_row(action_buttons, max_per_row=1))
        
        # Always add home button
        keyboard.extend(UIStandards.build_keyboard_row(['home'], max_per_row=1))
        
        await send_unified_message(
            user_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def _render_error_message(
        self,
        user_id: int,
        error_type: str,
        error_message: str,
        step: SceneStep,
        allow_retry: bool = True
    ) -> None:
        """Render standardized error message with consistent patterns"""
        try:
            # Get standardized error display configuration
            error_config = UIStandards.get_error_display(error_type, error_message, allow_retry)
            
            # Build error message text
            text = UIStandards.MESSAGE_TEMPLATES['header'].format(
                icon=error_config['icon'],
                title=error_config['title'],
                description="We encountered an issue while processing your request."
            )
            
            # Error details
            text += UIStandards.MESSAGE_TEMPLATES['error_message'].format(
                icon=error_config['icon'],
                error_type=error_config['title'],
                message=error_config['message']
            )
            text += "\n\n"
            
            # Additional help text
            if error_type == 'timeout_expired':
                text += f"{UIStandards.ICONS['info']} Your session has expired for security reasons.\n"
                text += "Please start over to continue with your transaction."
            elif error_type == 'validation_failed':
                text += f"{UIStandards.ICONS['info']} Please check your input and try again."
            elif error_type == 'network_error':
                text += f"{UIStandards.ICONS['info']} Please check your connection and try again."
            elif error_type == 'insufficient_balance':
                text += f"{UIStandards.ICONS['info']} Please ensure you have sufficient funds and try again."
            else:
                text += f"{UIStandards.ICONS['info']} Please try again or contact support if the issue persists."
            
            # Build action buttons
            action_buttons = []
            if error_config['retry_allowed']:
                action_buttons.append('retry')
            
            action_buttons.extend(['home'])
            
            # Add back button if step allows it
            if step.can_go_back and error_type not in ['timeout_expired']:
                action_buttons.insert(0, 'back')
            
            keyboard = UIStandards.build_keyboard_row(action_buttons, max_per_row=2)
            
            await send_unified_message(
                user_id=user_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
            )
        
        except Exception as e:
            logger.error(f"Error rendering error message: {e}")
            # Fallback minimal error message
            fallback_text = f"{UIStandards.ICONS['failed']} An error occurred. Please try again."
            try:
                await send_unified_message(
                    user_id=user_id,
                    text=fallback_text,
                    reply_markup=InlineKeyboardMarkup([
                        UIStandards.build_keyboard_row(['home'], max_per_row=1)[0]
                    ])
                )
            except Exception as fallback_error:
                logger.error(f"Fallback error message failed: {fallback_error}")
    
    async def _render_custom_keyboard(
        self,
        user_id: int,
        scene_state: SceneState,
        component_config: ComponentConfig,
        step: SceneStep
    ) -> None:
        """Render standardized custom keyboard component"""
        config = component_config.config
        buttons = config.get('buttons', [])
        
        # Standardized header
        text = UIStandards.MESSAGE_TEMPLATES['header'].format(
            icon=UIStandards.ICONS['select'],
            title=step.title,
            description=step.description
        )
        
        # Selection prompt
        text += UIStandards.MESSAGE_TEMPLATES['selection_prompt'].format(
            icon=UIStandards.ICONS['input']
        )
        
        # Add timeout warning
        text += "\n" + UIStandards.get_timeout_message('selection', 
                                                   UIStandards.TIMEOUT_SETTINGS['selection']['seconds'])
        
        # Build keyboard from button configuration
        keyboard = []
        row = []
        max_per_row = config.get('max_per_row', 2)
        
        for button in buttons:
            button_text = button.get('text', 'Option')
            if button.get('emoji'):
                button_text = f"{button['emoji']} {button_text}"
            
            row.append(InlineKeyboardButton(button_text, callback_data=button['data']))
            if len(row) >= max_per_row:
                keyboard.append(row)
                row = []
        
        if row:  # Add remaining buttons
            keyboard.append(row)
        
        # Add standardized navigation
        nav_buttons = ['back'] if step.can_go_back else []
        if nav_buttons:
            keyboard.extend(UIStandards.build_keyboard_row(nav_buttons, max_per_row=1))
        
        await send_unified_message(
            user_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )
    
    async def _render_text_input(
        self,
        user_id: int,
        scene_state: SceneState,
        component_config: ComponentConfig,
        step: SceneStep
    ) -> None:
        """Render standardized text input component"""
        config = component_config.config
        
        # Standardized header
        text = UIStandards.MESSAGE_TEMPLATES['header'].format(
            icon=UIStandards.ICONS['input'],
            title=step.title,
            description=step.description
        )
        
        # Input instructions
        input_type = config.get('input_type', 'text')
        if config.get('placeholder'):
            text += f"Example: {config['placeholder']}\n\n"
        
        # Input prompt based on type
        if input_type == 'otp':
            text += UIStandards.MESSAGE_TEMPLATES['input_prompt'].format(
                icon=UIStandards.ICONS['verify'],
                input_type="your OTP code"
            )
            text += f"\n{UIStandards.ICONS['info']} Check your phone for the verification code."
        else:
            text += UIStandards.MESSAGE_TEMPLATES['input_prompt'].format(
                icon=UIStandards.ICONS['input'],
                input_type="your response"
            )
        
        # Add timeout warning
        timeout_type = 'otp_verification' if input_type == 'otp' else 'text_input'
        text += "\n" + UIStandards.get_timeout_message(timeout_type, 
                                                   UIStandards.TIMEOUT_SETTINGS[timeout_type]['seconds'])
        
        # Add standardized navigation
        nav_buttons = ['back'] if step.can_go_back else []
        keyboard = UIStandards.build_keyboard_row(nav_buttons, max_per_row=1) if nav_buttons else []
        
        await send_unified_message(
            user_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )
    
    async def _render_selection_menu(
        self,
        user_id: int,
        scene_state: SceneState,
        component_config: ComponentConfig,
        step: SceneStep
    ) -> None:
        """Render standardized selection menu component"""
        config = component_config.config
        options = config.get('options', [])
        
        # Standardized header
        text = UIStandards.MESSAGE_TEMPLATES['header'].format(
            icon=UIStandards.ICONS['select'],
            title=step.title,
            description=step.description
        )
        
        # Available options section
        if options:
            text += UIStandards.MESSAGE_TEMPLATES['section_header'].format(
                icon=UIStandards.ICONS['select'],
                section_name="Available Options"
            )
            
            keyboard = []
            config.get('max_per_row', 1)
            
            for i, option in enumerate(options):
                # Show option description if available
                option_text = f"{i+1}. {option['label']}"
                if option.get('description'):
                    option_text += f" - {option['description']}"
                text += option_text + "\n"
                
                # Create button with appropriate emoji
                button_text = option['label']
                if 'emoji' in option:
                    button_text = f"{option['emoji']} {button_text}"
                
                keyboard.append([InlineKeyboardButton(
                    button_text, 
                    callback_data=f"select_{option['value']}"
                )])
            
            text += "\n"
        else:
            text += f"{UIStandards.ICONS['warning']} No options available.\n\n"
            keyboard = []
        
        # Selection prompt
        text += UIStandards.MESSAGE_TEMPLATES['selection_prompt'].format(
            icon=UIStandards.ICONS['input']
        )
        
        # Add timeout warning
        text += "\n" + UIStandards.get_timeout_message('selection', 
                                                   UIStandards.TIMEOUT_SETTINGS['selection']['seconds'])
        
        # Add standardized navigation
        nav_buttons = ['back'] if step.can_go_back else []
        if nav_buttons:
            keyboard.extend(UIStandards.build_keyboard_row(nav_buttons, max_per_row=1))
        
        await send_unified_message(
            user_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )