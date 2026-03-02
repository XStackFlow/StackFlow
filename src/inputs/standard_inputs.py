from typing import Any, Annotated, TypeVar, Generic, List, Dict
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

class Resolvable(Generic[T]):
    """A generic input that can be resolved from state templates."""
    def __init__(self, value: Any):
        self.value = value
    
    def __repr__(self):
        return f"Resolvable({self.value})"

JSONString = Annotated[str, "json_type"]
TemplateString = Annotated[str, "template_type"]

def resolve_attributes(obj: Any, state: Dict[str, Any]):
    """Resolves template strings in all instance attributes of an object using the state.
    
    Args:
        obj: The object whose attributes to resolve (usually a Node).
        state: The state dictionary for template resolution.
    """
    import inspect
    import json
    from typing import get_origin, get_args, Annotated
    from src.utils import template_manager
    
    # 1. Get type hints from constructor to know intended types
    try:
        sig = inspect.signature(obj.__init__)
        type_hints = {name: param.annotation for name, param in sig.parameters.items()}
    except Exception:
        type_hints = {}
    
    name = getattr(obj, "node_name", obj.__class__.__name__)
    
    for key, value in list(obj.__dict__.items()):
        # Skip internal/private attributes
        if key.startswith("_"):
            continue

        # 2. Check if this field is Resolvable[T]
        hint = type_hints.get(key)
        origin = get_origin(hint) or hint
        is_resolvable = origin is Resolvable or (
            inspect.isclass(origin) and issubclass(origin, Resolvable)
        )

        # 3. Non-Resolvable fields are passed through as-is (no template resolution)
        if not is_resolvable:
            setattr(obj, f"_{key}", value)
            continue

        # 4. Unwrap Resolvable[T] to determine the target cast type
        base_cast_type = None
        args = get_args(hint)
        if args:
            inner_type = args[0]
            # Unwrap Annotated if present
            while get_origin(inner_type) == Annotated:
                inner_type = get_args(inner_type)[0]
            base_cast_type = inner_type

        # 5. Access the raw template value and resolve
        raw_value = value.value if isinstance(value, Resolvable) else value
        resolved = template_manager.render_template(raw_value, state)

        # 6. Type cast based on base_cast_type
        if base_cast_type is float:
            try:
                resolved = float(resolved) if resolved is not None and str(resolved).strip() != "" else 0.0
            except (ValueError, TypeError):
                logger.warning(f"{name}: Could not cast '{resolved}' to float for {key}. Defaulting to 0.0")
                resolved = 0.0
        elif base_cast_type is int:
            try:
                resolved = int(resolved) if resolved is not None and str(resolved).strip() != "" else 0
            except (ValueError, TypeError):
                logger.warning(f"{name}: Could not cast '{resolved}' to int for {key}. Defaulting to 0")
                resolved = 0
        elif base_cast_type is bool:
            if isinstance(resolved, str):
                resolved = resolved.lower() in ("true", "1", "yes", "on")
            else:
                resolved = bool(resolved)
        elif base_cast_type is list or (get_origin(base_cast_type) is list):
            if not isinstance(resolved, list):
                try:
                    if isinstance(resolved, str) and resolved.strip().startswith("["):
                        resolved = json.loads(resolved)
                    elif isinstance(resolved, str):
                        if not resolved.strip():
                            resolved = []
                        else:
                            # Handle comma-separated strings
                            resolved = [k.strip() for k in resolved.split(",") if k.strip()]
                except Exception:
                    pass
            if not isinstance(resolved, list):
                resolved = [resolved] if resolved is not None and resolved != "" else []
        elif base_cast_type is dict or (get_origin(base_cast_type) is dict):
            if not isinstance(resolved, dict):
                try:
                    if isinstance(resolved, str) and resolved.strip().startswith("{"):
                        # Try JSON first (standard)
                        try:
                            resolved = json.loads(resolved)
                        except json.JSONDecodeError:
                            # Fallback to literal_eval for Python-style dict strings (single quotes)
                            import ast
                            resolved = ast.literal_eval(resolved)
                except Exception:
                    pass
            if not isinstance(resolved, dict):
                logger.warning(f"{name}: Could not cast '{resolved}' to dict for {key}. Defaulting to {{}}")
                resolved = {}
        
        setattr(obj, f"_{key}", resolved)
