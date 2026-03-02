from typing import Optional
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)

async def resolve_checkpoint_ns(graph_runnable, thread_id: str, logical_path: Optional[str]) -> tuple[str, str, bool]:
    """
    Resolves a logical subgraph path (e.g. SubA@@SubB) to a real LangGraph checkpoint_ns.
    Returns: (target_ns, residual_prefix, resolved_all)
    """
    # Special case: empty or "root" path
    if not logical_path or logical_path.lower() == "root":
        return "", "", True

    # Strip (GraphName) suffixes if present (Frontend adds them for display)
    import re
    clean_path = "@@".join([re.sub(r'\(.*?\)', '', s) for s in logical_path.split('@@')])
    
    nodes_path = clean_path.split('@@')
    target_ns = ""
    residual_prefix = ""
    resolved_all = True
    
    # We track the "logical prefix" within the current checkpoint_ns.
    # When we jump namespace, this prefix resets.
    current_ns_local_prefix = ""

    for segment_idx, node_id in enumerate(nodes_path):
        # The node name we are looking for is prefixed by any inlined parents in this NS.
        look_for = f"{current_ns_local_prefix}@@{node_id}" if current_ns_local_prefix else node_id
        
        try:
            lookup_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": target_ns}}
            state_snapshot = await graph_runnable.aget_state(lookup_config)
            found = False
            
            logger.info("[DEBUG_NS] Step '%s': target_ns='%s', look_for='%s', tasks=%s", 
                        node_id, target_ns, look_for, [t.name for t in state_snapshot.tasks])
            
            # 1. Search current tasks
            for task in state_snapshot.tasks:
                if task.name == look_for:
                    found = True
                    task_ns = task.state.get("configurable", {}).get("checkpoint_ns") if task.state else None
                    if task_ns:
                        # Namespace Jump!
                        target_ns = task_ns
                        current_ns_local_prefix = ""
                    else:
                        # Exact match but no namespace change (normal node or inlined start)
                        current_ns_local_prefix = look_for
                    break
                elif task.name.startswith(look_for + "@@"):
                    # Prefix match: we are entering an inlined subgraph
                    found = True
                    current_ns_local_prefix = look_for
                    break
            
            # 2. Search history if not in current tasks
            if not found:
                async for snapshot in graph_runnable.aget_state_history(lookup_config, limit=50):
                    for task in snapshot.tasks:
                        if task.name == look_for:
                            found = True
                            task_ns = task.state.get("configurable", {}).get("checkpoint_ns") if task.state else None
                            if task_ns:
                                target_ns = task_ns
                                current_ns_local_prefix = ""
                            else:
                                current_ns_local_prefix = look_for
                            break
                        elif task.name.startswith(look_for + "@@"):
                            found = True
                            current_ns_local_prefix = look_for
                            break
                    if found: break
            
            if not found:
                logger.warning("FAILED TO RESOLVE NS for segment '%s' in path '%s' (current ns: '%s', looking for '%s')", 
                             node_id, clean_path, target_ns, look_for)
                resolved_all = False
                break
                
        except Exception as e:
            logger.error("STATE DISCOVERY ERROR at segment '%s': %s", node_id, e)
            resolved_all = False
            break
            
    residual_prefix = current_ns_local_prefix
    return target_ns, residual_prefix, resolved_all
