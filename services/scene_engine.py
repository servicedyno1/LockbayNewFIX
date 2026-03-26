"""
Telegram Scene Engine - Core Architecture

A declarative system that replaces 57+ complex handlers with simple, reusable flow definitions.
Provides component-based UI, state management, and intelligent message routing.

Key Features:
- Declarative scene definitions (JSON/Python dict based)
- Reusable UI components (amount_input, bank_selector, etc.)
- Smart state management with user progress tracking
- Intelligent message routing and validation
- Full UTE integration for financial operations
- Provider adapter integration for external services

Architecture:
    SceneDefinition -> SceneInstance -> ComponentRenderer -> UserInteraction
                    -> StateManager -> MessageRouter -> UTE/Providers
"""

import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import ContextTypes

# Import existing infrastructure
from services.unified_transaction_engine import (
    UnifiedTransactionEngine
)

logger = logging.getLogger(__name__)

# ===== SCENE ENGINE ENUMS AND TYPES =====

class SceneStatus(Enum):
    """Scene execution status"""
    INACTIVE = "inactive"
    ACTIVE = "active"
    WAITING_INPUT = "waiting_input"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class ComponentType(Enum):
    """Types of UI components available"""
    AMOUNT_INPUT = "amount_input"
    ADDRESS_SELECTOR = "address_selector"
    BANK_SELECTOR = "bank_selector"
    CONFIRMATION = "confirmation"
    STATUS_DISPLAY = "status_display"
    CUSTOM_KEYBOARD = "custom_keyboard"
    TEXT_INPUT = "text_input"
    SELECTION_MENU = "selection_menu"

class MessageType(Enum):
    """Types of messages that can be processed"""
    TEXT = "text"
    CALLBACK_QUERY = "callback_query"
    DOCUMENT = "document"
    PHOTO = "photo"

@dataclass
class SceneState:
    """Current state of a scene instance"""
    scene_id: str
    user_id: int
    current_step: str
    status: SceneStatus
    data: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    timeout_at: Optional[datetime] = None

@dataclass
class ComponentConfig:
    """Configuration for a UI component"""
    component_type: ComponentType
    config: Dict[str, Any] = field(default_factory=dict)
    validation: Optional[Dict[str, Any]] = None
    on_success: Optional[str] = None  # Next step on success
    on_error: Optional[str] = None    # Step on error/retry

@dataclass
class SceneStep:
    """Definition of a single step in a scene"""
    step_id: str
    title: str
    description: str
    components: List[ComponentConfig]
    timeout_seconds: Optional[int] = None
    retry_count: int = 3
    can_go_back: bool = True

@dataclass
class SceneDefinition:
    """Complete definition of a scene flow"""
    scene_id: str
    name: str
    description: str
    steps: List[SceneStep]
    initial_step: str
    final_steps: List[str]  # Steps that mark completion
    integrations: List[str] = field(default_factory=list)  # Required integrations
    
# ===== SCENE STATE MANAGER =====

class SceneStateManager:
    """Manages scene state persistence and retrieval"""
    
    def __init__(self):
        self._active_scenes: Dict[int, SceneState] = {}
        self._scene_history: Dict[int, List[SceneState]] = {}
    
    async def create_scene_instance(
        self, 
        scene_id: str, 
        user_id: int, 
        initial_step: str,
        timeout_minutes: int = 30
    ) -> SceneState:
        """Create a new scene instance for a user"""
        # Clean up any existing scene for this user
        await self.cleanup_user_scenes(user_id)
        
        scene_state = SceneState(
            scene_id=scene_id,
            user_id=user_id,
            current_step=initial_step,
            status=SceneStatus.ACTIVE,
            timeout_at=datetime.utcnow() + timedelta(minutes=timeout_minutes)
        )
        
        self._active_scenes[user_id] = scene_state
        
        # Initialize history
        if user_id not in self._scene_history:
            self._scene_history[user_id] = []
        
        logger.info(f"Created scene instance: {scene_id} for user {user_id}")
        return scene_state
    
    async def get_active_scene(self, user_id: int) -> Optional[SceneState]:
        """Get the active scene for a user"""
        scene = self._active_scenes.get(user_id)
        if scene and scene.timeout_at and datetime.utcnow() > scene.timeout_at:
            logger.warning(f"Scene {scene.scene_id} timed out for user {user_id}")
            await self.mark_scene_completed(user_id, SceneStatus.FAILED, "Scene timed out")
            return None
        return scene
    
    async def update_scene_step(
        self, 
        user_id: int, 
        new_step: str, 
        data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Update the current step of an active scene"""
        scene = await self.get_active_scene(user_id)
        if not scene:
            return False
        
        scene.current_step = new_step
        scene.updated_at = datetime.utcnow()
        
        if data:
            scene.data.update(data)
        
        logger.info(f"Updated scene {scene.scene_id} to step {new_step} for user {user_id}")
        return True
    
    async def update_scene_data(self, user_id: int, data: Dict[str, Any]) -> bool:
        """Update scene data without changing step"""
        scene = await self.get_active_scene(user_id)
        if not scene:
            return False
        
        scene.data.update(data)
        scene.updated_at = datetime.utcnow()
        return True
    
    async def add_scene_error(self, user_id: int, error: str) -> bool:
        """Add an error to the scene"""
        scene = await self.get_active_scene(user_id)
        if not scene:
            return False
        
        scene.errors.append(f"{datetime.utcnow().isoformat()}: {error}")
        scene.updated_at = datetime.utcnow()
        return True
    
    async def mark_scene_completed(
        self, 
        user_id: int, 
        status: SceneStatus, 
        result: Optional[str] = None
    ) -> bool:
        """Mark a scene as completed and archive it"""
        scene = self._active_scenes.get(user_id)
        if not scene:
            return False
        
        scene.status = status
        scene.updated_at = datetime.utcnow()
        
        if result:
            scene.data['final_result'] = result
        
        # Move to history
        self._scene_history[user_id].append(scene)
        
        # Remove from active
        del self._active_scenes[user_id]
        
        logger.info(f"Scene {scene.scene_id} completed with status {status} for user {user_id}")
        return True
    
    async def cleanup_user_scenes(self, user_id: int) -> None:
        """Clean up all scenes for a user"""
        if user_id in self._active_scenes:
            scene = self._active_scenes[user_id]
            logger.info(f"Cleaning up active scene {scene.scene_id} for user {user_id}")
            await self.mark_scene_completed(user_id, SceneStatus.CANCELLED, "Scene cleanup")

# ===== MESSAGE ROUTER =====

class SceneMessageRouter:
    """Routes messages to appropriate scene components"""
    
    def __init__(self, state_manager: SceneStateManager):
        self.state_manager = state_manager
        self.scene_registry: Dict[str, SceneDefinition] = {}
    
    def register_scene(self, scene: SceneDefinition) -> None:
        """Register a scene definition"""
        self.scene_registry[scene.scene_id] = scene
        logger.info(f"Registered scene: {scene.scene_id}")
    
    async def route_message(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        """Route a message to the appropriate scene component"""
        if not update.effective_user:
            return False
        
        user_id = update.effective_user.id
        scene_state = await self.state_manager.get_active_scene(user_id)
        
        if not scene_state:
            return False  # No active scene
        
        scene_def = self.scene_registry.get(scene_state.scene_id)
        if not scene_def:
            logger.error(f"Scene definition not found: {scene_state.scene_id}")
            return False
        
        # Find current step
        current_step = None
        for step in scene_def.steps:
            if step.step_id == scene_state.current_step:
                current_step = step
                break
        
        if not current_step:
            logger.error(f"Step not found: {scene_state.current_step}")
            return False
        
        # Process message with components
        return await self._process_step_message(
            update, context, scene_state, current_step
        )
    
    async def _process_step_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        scene_state: SceneState,
        step: SceneStep
    ) -> bool:
        """Process a message for a specific step"""
        try:
            # Import component processors
            from components.component_processor import ComponentProcessor
            processor = ComponentProcessor(self.state_manager)
            
            # Process with each component until one handles it
            for component_config in step.components:
                handled = await processor.process_component(
                    update, context, scene_state, component_config
                )
                if handled:
                    return True
            
            # No component handled the message
            logger.warning(f"No component handled message in step {step.step_id}")
            return False
            
        except Exception as e:
            logger.error(f"Error processing step message: {e}")
            await self.state_manager.add_scene_error(
                scene_state.user_id, f"Step processing error: {e}"
            )
            return False

# ===== SCENE ENGINE CORE =====

class SceneEngine:
    """Core Scene Engine - orchestrates all scene operations"""
    
    def __init__(self):
        self.state_manager = SceneStateManager()
        self.message_router = SceneMessageRouter(self.state_manager)
        self.ute_engine = UnifiedTransactionEngine()
        self._initialized = False
    
    async def initialize(self) -> None:
        """Initialize the Scene Engine and load scene definitions"""
        if self._initialized:
            return
        
        try:
            # Load all scene definitions
            await self._load_scene_definitions()
            
            # UTE doesn't need explicit initialization
            logger.info("UTE integration ready")
            
            self._initialized = True
            logger.info("Scene Engine initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize Scene Engine: {e}")
            raise
    
    async def _load_scene_definitions(self) -> None:
        """Load all scene definitions from the scenes directory"""
        # Import and register scene definitions
        from scenes.ngn_cashout import ngn_cashout_scene
        from scenes.crypto_cashout import crypto_cashout_scene
        from scenes.wallet_funding import wallet_funding_scene
        from scenes.escrow_creation import escrow_creation_scene
        
        scenes = [
            ngn_cashout_scene,
            crypto_cashout_scene, 
            wallet_funding_scene,
            escrow_creation_scene
        ]
        
        for scene in scenes:
            self.message_router.register_scene(scene)
        
        logger.info(f"Loaded {len(scenes)} scene definitions")
    
    async def start_scene(
        self, 
        scene_id: str, 
        user_id: int, 
        initial_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Start a new scene for a user"""
        try:
            if not self._initialized:
                await self.initialize()
            
            scene_def = self.message_router.scene_registry.get(scene_id)
            if not scene_def:
                logger.error(f"Scene not found: {scene_id}")
                return False
            
            # Create scene instance
            await self.state_manager.create_scene_instance(
                scene_id, user_id, scene_def.initial_step
            )
            
            if initial_data:
                await self.state_manager.update_scene_data(user_id, initial_data)
            
            # Render initial step
            await self._render_current_step(user_id)
            
            logger.info(f"Started scene {scene_id} for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start scene {scene_id}: {e}")
            return False
    
    async def handle_message(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        """Handle an incoming message for any active scene"""
        if not self._initialized:
            await self.initialize()
        
        return await self.message_router.route_message(update, context)
    
    async def _render_current_step(self, user_id: int) -> None:
        """Render the current step for a user"""
        scene_state = await self.state_manager.get_active_scene(user_id)
        if not scene_state:
            return
        
        scene_def = self.message_router.scene_registry.get(scene_state.scene_id)
        if not scene_def:
            return
        
        # Find current step
        current_step = None
        for step in scene_def.steps:
            if step.step_id == scene_state.current_step:
                current_step = step
                break
        
        if not current_step:
            return
        
        try:
            # Import component renderer
            from components.component_renderer import ComponentRenderer
            renderer = ComponentRenderer()
            
            # Render all components for this step
            for component_config in current_step.components:
                await renderer.render_component(
                    user_id, scene_state, component_config, current_step
                )
        
        except Exception as e:
            logger.error(f"Failed to render step {current_step.step_id}: {e}")
    
    async def cancel_scene(self, user_id: int) -> bool:
        """Cancel the active scene for a user"""
        scene_state = await self.state_manager.get_active_scene(user_id)
        if not scene_state:
            return False
        
        await self.state_manager.mark_scene_completed(
            user_id, SceneStatus.CANCELLED, "User cancelled"
        )
        
        logger.info(f"Cancelled scene {scene_state.scene_id} for user {user_id}")
        return True
    
    async def get_scene_status(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get the status of the active scene for a user"""
        scene_state = await self.state_manager.get_active_scene(user_id)
        if not scene_state:
            return None
        
        return {
            "scene_id": scene_state.scene_id,
            "current_step": scene_state.current_step,
            "status": scene_state.status.value,
            "data": scene_state.data,
            "created_at": scene_state.created_at.isoformat(),
            "updated_at": scene_state.updated_at.isoformat()
        }

# ===== GLOBAL SCENE ENGINE INSTANCE =====

# Global scene engine instance
_scene_engine = None

async def get_scene_engine() -> SceneEngine:
    """Get the global scene engine instance"""
    global _scene_engine
    if _scene_engine is None:
        _scene_engine = SceneEngine()
        await _scene_engine.initialize()
    return _scene_engine

async def handle_scene_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle message routing for scenes - called from main handlers"""
    engine = await get_scene_engine()
    return await engine.handle_message(update, context)

async def start_user_scene(
    scene_id: str, 
    user_id: int, 
    initial_data: Optional[Dict[str, Any]] = None
) -> bool:
    """Start a scene for a user - called from existing handlers"""
    engine = await get_scene_engine()
    return await engine.start_scene(scene_id, user_id, initial_data)

logger.info("Scene Engine core loaded successfully")