"""
Component Processor - Handles component message processing

Routes messages to appropriate component handlers based on component type.
Provides consistent validation and error handling across all components.
"""

import logging
from typing import Dict, Any
from telegram import Update
from telegram.ext import ContextTypes

from services.scene_engine import SceneState, ComponentConfig, ComponentType
from .amount_input import AmountInputComponent
from .address_selector import AddressSelectorComponent
from .bank_selector import BankSelectorComponent
from .confirmation import ConfirmationComponent
from .status_display import StatusDisplayComponent

logger = logging.getLogger(__name__)

class ComponentProcessor:
    """Processes messages for Scene Engine components"""
    
    def __init__(self, state_manager):
        self.state_manager = state_manager
        self.component_handlers = {
            ComponentType.AMOUNT_INPUT: AmountInputComponent(),
            ComponentType.ADDRESS_SELECTOR: AddressSelectorComponent(),
            ComponentType.BANK_SELECTOR: BankSelectorComponent(), 
            ComponentType.CONFIRMATION: ConfirmationComponent(),
            ComponentType.STATUS_DISPLAY: StatusDisplayComponent(),
        }
    
    async def process_component(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        scene_state: SceneState,
        component_config: ComponentConfig
    ) -> bool:
        """Process a message with a specific component"""
        try:
            handler = self.component_handlers.get(component_config.component_type)
            if not handler:
                logger.error(f"No handler for component type: {component_config.component_type}")
                return False
            
            # Process the message
            result = await handler.process_message(
                update, context, scene_state, component_config
            )
            
            if result and result.get('success'):
                # Handle successful processing
                await self._handle_success(scene_state, component_config, result)
                return True
            elif result and result.get('error'):
                # Handle error
                await self._handle_error(scene_state, component_config, result)
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Component processing error: {e}")
            await self.state_manager.add_scene_error(
                scene_state.user_id, f"Component error: {e}"
            )
            return False
    
    async def _handle_success(
        self, 
        scene_state: SceneState, 
        component_config: ComponentConfig, 
        result: Dict[str, Any]
    ) -> None:
        """Handle successful component processing"""
        # Update scene data with result
        if 'data' in result:
            await self.state_manager.update_scene_data(
                scene_state.user_id, result['data']
            )
        
        # Move to next step if specified
        if component_config.on_success:
            await self.state_manager.update_scene_step(
                scene_state.user_id, component_config.on_success
            )
    
    async def _handle_error(
        self,
        scene_state: SceneState,
        component_config: ComponentConfig, 
        result: Dict[str, Any]
    ) -> None:
        """Handle component processing error"""
        error_msg = result.get('error', 'Unknown error')
        await self.state_manager.add_scene_error(scene_state.user_id, error_msg)
        
        # Move to error step if specified, otherwise stay on current step
        if component_config.on_error:
            await self.state_manager.update_scene_step(
                scene_state.user_id, component_config.on_error
            )