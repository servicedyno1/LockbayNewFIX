"""
Handler Decorator Enforcement System
Ensures 100% audit coverage by enforcing decorators on all handlers and providing comprehensive validation
"""

import logging
import ast
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum


logger = logging.getLogger(__name__)


class HandlerType(Enum):
    """Types of handlers that require specific decorators"""
    USER_INTERACTION = "user_interaction"
    ADMIN_ACTION = "admin_action"
    CONVERSATION = "conversation"
    COMMUNICATION = "communication"
    TRANSACTION = "transaction"
    SYSTEM = "system"
    WEBHOOK = "webhook"


@dataclass
class HandlerInfo:
    """Information about a handler function"""
    name: str
    file_path: str
    line_number: int
    handler_type: HandlerType
    required_decorators: List[str]
    current_decorators: List[str]
    is_decorated: bool
    is_async: bool
    has_update_param: bool
    has_context_param: bool


class HandlerDecoratorEnforcer:
    """
    Enforces comprehensive audit decorator coverage across all handlers
    """
    
    def __init__(self):
        self.handlers_dir = Path("handlers")
        self.required_decorators = {
            HandlerType.USER_INTERACTION: ["handler_lifecycle"],
            HandlerType.ADMIN_ACTION: ["admin_action", "handler_lifecycle"],
            HandlerType.CONVERSATION: ["conversation_handler", "handler_lifecycle"],
            HandlerType.COMMUNICATION: ["communication_handler", "handler_lifecycle"],
            HandlerType.TRANSACTION: ["transaction_handler", "handler_lifecycle"],
            HandlerType.SYSTEM: ["system_handler"],
            HandlerType.WEBHOOK: ["system_handler"]
        }
        
        # Handler name patterns to identify types
        self.handler_patterns = {
            HandlerType.ADMIN_ACTION: [
                "admin_", "Admin", "ADMIN", "admin.*handler", "admin.*action"
            ],
            HandlerType.CONVERSATION: [
                "conversation", "conv_", "dialog", "state_", "flow_"
            ],
            HandlerType.COMMUNICATION: [
                "message", "chat", "send_", "notify", "broadcast", "communication"
            ],
            HandlerType.TRANSACTION: [
                "escrow", "wallet", "payment", "cashout", "transaction", "financial",
                "exchange", "trade", "deposit", "withdrawal", "refund"
            ],
            HandlerType.WEBHOOK: [
                "webhook", "callback", "notification_", "webhook_handler"
            ]
        }
    
    def analyze_handler_coverage(self) -> Dict[str, Any]:
        """
        Analyze all handlers and return comprehensive coverage report
        
        Returns:
            Dictionary with analysis results
        """
        logger.info("🔍 Starting comprehensive handler decorator analysis...")
        
        handler_files = self._get_handler_files()
        all_handlers = []
        coverage_stats = {
            "total_handlers": 0,
            "decorated_handlers": 0,
            "undecorated_handlers": 0,
            "coverage_percentage": 0.0,
            "missing_decorators": [],
            "handler_types": {},
            "files_analyzed": len(handler_files)
        }
        
        for file_path in handler_files:
            try:
                handlers = self._analyze_file(file_path)
                all_handlers.extend(handlers)
                logger.debug(f"📄 Analyzed {file_path}: {len(handlers)} handlers found")
            except Exception as e:
                logger.error(f"❌ Failed to analyze {file_path}: {e}")
        
        # Calculate coverage statistics
        coverage_stats["total_handlers"] = len(all_handlers)
        decorated_count = sum(1 for h in all_handlers if h.is_decorated)
        coverage_stats["decorated_handlers"] = decorated_count
        coverage_stats["undecorated_handlers"] = len(all_handlers) - decorated_count
        
        if len(all_handlers) > 0:
            coverage_stats["coverage_percentage"] = (decorated_count / len(all_handlers)) * 100
        
        # Group by handler types
        for handler in all_handlers:
            handler_type = handler.handler_type.value
            if handler_type not in coverage_stats["handler_types"]:
                coverage_stats["handler_types"][handler_type] = {
                    "total": 0,
                    "decorated": 0,
                    "coverage": 0.0
                }
            
            coverage_stats["handler_types"][handler_type]["total"] += 1
            if handler.is_decorated:
                coverage_stats["handler_types"][handler_type]["decorated"] += 1
        
        # Calculate per-type coverage
        for handler_type_stats in coverage_stats["handler_types"].values():
            if handler_type_stats["total"] > 0:
                handler_type_stats["coverage"] = (
                    handler_type_stats["decorated"] / handler_type_stats["total"]
                ) * 100
        
        # Find missing decorators
        coverage_stats["missing_decorators"] = [
            {
                "handler": h.name,
                "file": h.file_path,
                "line": h.line_number,
                "type": h.handler_type.value,
                "required": h.required_decorators,
                "current": h.current_decorators
            }
            for h in all_handlers if not h.is_decorated
        ]
        
        logger.info(f"✅ Analysis complete: {coverage_stats['coverage_percentage']:.1f}% coverage "
                   f"({decorated_count}/{len(all_handlers)} handlers decorated)")
        
        return {
            "handlers": all_handlers,
            "stats": coverage_stats
        }
    
    def enforce_decorators(self, dry_run: bool = True) -> Dict[str, Any]:
        """
        Enforce decorators on all handlers
        
        Args:
            dry_run: If True, only report what would be changed
            
        Returns:
            Dictionary with enforcement results
        """
        logger.info(f"🛠️ Starting decorator enforcement (dry_run={dry_run})...")
        
        analysis = self.analyze_handler_coverage()
        handlers = analysis["handlers"]
        
        enforcement_results = {
            "files_modified": 0,
            "decorators_added": 0,
            "errors": [],
            "changes": []
        }
        
        # Group handlers by file for efficient processing
        handlers_by_file = {}
        for handler in handlers:
            if not handler.is_decorated:
                if handler.file_path not in handlers_by_file:
                    handlers_by_file[handler.file_path] = []
                handlers_by_file[handler.file_path].append(handler)
        
        for file_path, file_handlers in handlers_by_file.items():
            try:
                if dry_run:
                    # Just report what would be changed
                    for handler in file_handlers:
                        enforcement_results["changes"].append({
                            "file": file_path,
                            "handler": handler.name,
                            "line": handler.line_number,
                            "decorators_to_add": handler.required_decorators
                        })
                        enforcement_results["decorators_added"] += len(handler.required_decorators)
                else:
                    # Actually modify the file
                    self._add_decorators_to_file(file_path, file_handlers)
                    enforcement_results["files_modified"] += 1
                    enforcement_results["decorators_added"] += sum(
                        len(h.required_decorators) for h in file_handlers
                    )
                
            except Exception as e:
                error_msg = f"Failed to process {file_path}: {e}"
                logger.error(f"❌ {error_msg}")
                enforcement_results["errors"].append(error_msg)
        
        if dry_run:
            logger.info(f"📋 Dry run complete: Would modify {len(handlers_by_file)} files, "
                       f"add {enforcement_results['decorators_added']} decorators")
        else:
            logger.info(f"✅ Enforcement complete: Modified {enforcement_results['files_modified']} files, "
                       f"added {enforcement_results['decorators_added']} decorators")
        
        return enforcement_results
    
    def _get_handler_files(self) -> List[Path]:
        """Get all Python files in handlers directory"""
        if not self.handlers_dir.exists():
            logger.warning(f"Handlers directory not found: {self.handlers_dir}")
            return []
        
        return [
            f for f in self.handlers_dir.rglob("*.py") 
            if not f.name.startswith("_") and f.name != "__init__.py"
        ]
    
    def _analyze_file(self, file_path: Path) -> List[HandlerInfo]:
        """Analyze a single file for handler functions"""
        handlers = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Parse AST
            tree = ast.parse(content)
            
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name.endswith('_handler'):
                    handler_info = self._analyze_function(node, file_path, content)
                    if handler_info:
                        handlers.append(handler_info)
        
        except Exception as e:
            logger.error(f"Failed to parse {file_path}: {e}")
        
        return handlers
    
    def _analyze_function(self, func_node: ast.FunctionDef, file_path: Path, content: str) -> Optional[HandlerInfo]:
        """Analyze a single function to determine if it's a handler"""
        
        # Check if it's a handler function
        if not self._is_handler_function(func_node):
            return None
        
        # Determine handler type
        handler_type = self._determine_handler_type(func_node.name, str(file_path))
        
        # Get current decorators
        current_decorators = [
            self._get_decorator_name(decorator) 
            for decorator in func_node.decorator_list
        ]
        
        # Get required decorators
        required_decorators = self.required_decorators.get(handler_type, [])
        
        # Check if properly decorated
        is_decorated = all(
            any(req in current for current in current_decorators)
            for req in required_decorators
        )
        
        # Check function signature
        has_update_param = any(
            arg.arg in ['update', 'Update'] for arg in func_node.args.args
        )
        has_context_param = any(
            arg.arg in ['context', 'Context'] for arg in func_node.args.args
        )
        
        return HandlerInfo(
            name=func_node.name,
            file_path=str(file_path),
            line_number=func_node.lineno,
            handler_type=handler_type,
            required_decorators=required_decorators,
            current_decorators=current_decorators,
            is_decorated=is_decorated,
            is_async=isinstance(func_node, ast.AsyncFunctionDef),
            has_update_param=has_update_param,
            has_context_param=has_context_param
        )
    
    def _is_handler_function(self, func_node: ast.FunctionDef) -> bool:
        """Check if function is a Telegram handler"""
        return (
            func_node.name.endswith('_handler') or
            func_node.name.endswith('_callback') or
            func_node.name.startswith('handle_') or
            any(self._get_decorator_name(dec) in ['handler_lifecycle', 'admin_action', 'conversation_handler']
                for dec in func_node.decorator_list)
        )
    
    def _determine_handler_type(self, func_name: str, file_path: str) -> HandlerType:
        """Determine the type of handler based on name and file path"""
        
        # Check patterns
        for handler_type, patterns in self.handler_patterns.items():
            for pattern in patterns:
                if pattern in func_name.lower() or pattern in file_path.lower():
                    return handler_type
        
        # Default to user interaction
        return HandlerType.USER_INTERACTION
    
    def _get_decorator_name(self, decorator_node: ast.expr) -> str:
        """Extract decorator name from AST node"""
        if isinstance(decorator_node, ast.Name):
            return decorator_node.id
        elif isinstance(decorator_node, ast.Attribute):
            return decorator_node.attr
        elif isinstance(decorator_node, ast.Call):
            if isinstance(decorator_node.func, ast.Name):
                return decorator_node.func.id
            elif isinstance(decorator_node.func, ast.Attribute):
                return decorator_node.func.attr
        return str(decorator_node)
    
    def _add_decorators_to_file(self, file_path: str, handlers: List[HandlerInfo]) -> None:
        """Add required decorators to handlers in a file"""
        
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Add imports if needed
        needed_imports = set()
        for handler in handlers:
            needed_imports.update(handler.required_decorators)
        
        if needed_imports:
            import_line = f"from utils.handler_decorators import {', '.join(needed_imports)}\n"
            # Find the right place to add imports
            insert_pos = 0
            for i, line in enumerate(lines):
                if line.strip().startswith('from ') or line.strip().startswith('import '):
                    insert_pos = i + 1
            lines.insert(insert_pos, import_line)
        
        # Add decorators (working backwards to preserve line numbers)
        for handler in sorted(handlers, key=lambda h: h.line_number, reverse=True):
            decorator_lines = []
            for decorator in handler.required_decorators:
                decorator_lines.append(f"@{decorator}\n")
            
            # Insert decorators before the function definition
            insert_pos = handler.line_number - 1  # AST uses 1-based line numbers
            for decorator_line in reversed(decorator_lines):
                lines.insert(insert_pos, decorator_line)
        
        # Write back to file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        
        logger.info(f"✅ Added decorators to {len(handlers)} handlers in {file_path}")


# Global instance
handler_enforcer = HandlerDecoratorEnforcer()


def analyze_handler_coverage() -> Dict[str, Any]:
    """
    Convenience function to analyze handler coverage
    
    Returns:
        Analysis results
    """
    return handler_enforcer.analyze_handler_coverage()


def enforce_handler_decorators(dry_run: bool = True) -> Dict[str, Any]:
    """
    Convenience function to enforce handler decorators
    
    Args:
        dry_run: If True, only report what would be changed
        
    Returns:
        Enforcement results
    """
    return handler_enforcer.enforce_decorators(dry_run=dry_run)


def validate_handler_coverage() -> bool:
    """
    Validate that all handlers have proper decorators
    
    Returns:
        True if 100% coverage achieved
    """
    analysis = analyze_handler_coverage()
    coverage = analysis["stats"]["coverage_percentage"]
    
    if coverage >= 100.0:
        logger.info("✅ 100% handler decorator coverage achieved!")
        return True
    else:
        logger.warning(f"⚠️ Handler decorator coverage: {coverage:.1f}% "
                      f"({analysis['stats']['undecorated_handlers']} handlers missing decorators)")
        return False


if __name__ == "__main__":
    # Run analysis when executed directly
    results = analyze_handler_coverage()
    print(f"Handler Coverage: {results['stats']['coverage_percentage']:.1f}%")
    print(f"Total handlers: {results['stats']['total_handlers']}")
    print(f"Decorated: {results['stats']['decorated_handlers']}")
    print(f"Missing decorators: {results['stats']['undecorated_handlers']}")