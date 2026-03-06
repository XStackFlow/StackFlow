from typing import Any, Dict, List, Optional, Annotated
from collections import defaultdict
import re
from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy

from src.utils.setup.logger import get_logger
from src.nodes.abstract.router_node import RouterNode
from src.utils.exceptions import RetriableError
import json
import copy
import inspect
import asyncio
from src.utils.setup.const import GRAPH_SAVE_PATH
from src.utils.setup.module_registry import resolve_module_graph_path

logger = get_logger(__name__)


_DELETE_SENTINEL = "@delete"


def merge_dicts(left: dict, right: dict) -> dict:
    """Reducer function to merge two dictionaries.

    Used in fan-in patterns where multiple parallel branches update state.
    Right dict values overwrite left dict values for shared keys.
    If both values are dictionaries (e.g. namespaces), they are merged.
    Values equal to "@delete" remove the key from state.
    """
    if left is None:
        return right or {}
    if right is None:
        return left or {}

    merged = {**left}
    for k, v in right.items():
        if v == _DELETE_SENTINEL:
            merged.pop(k, None)
        elif k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            # Shallow merge for dictionaries/namespaces
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


def get_node_id(node_data):
    """Returns a unique ID for a node in the LangGraph structure."""
    if not node_data: return None
    node_type = node_data.get("type")
    
    props = node_data.get("properties", {})
    
    # Force uppercase for special types to match UI title defaults (START/END)
    if node_type == "langgraph/start":
        base_name = "START"
    elif node_type == "langgraph/end":
        base_name = "END"
    else:
        raw_name = props.get("name") or node_data.get("title") or node_type.split('/')[-1]
        # Proactively build the correct format for subgraphs before sanitization
        if node_type == "langgraph/subgraph":
            # Replace "SUBGRAPH: " (with optional space) with "SUBGRAPH@"
            base_name = re.sub(r'^SUBGRAPH:\s*', 'SUBGRAPH@', raw_name)
        else:
            base_name = raw_name
    
    # Sanitize name: LangGraph reserved characters (like ':') are not allowed.
    # We replace any non-alphanumeric character (except underscore and @) with an underscore.
    base_name = re.sub(r'[^a-zA-Z0-9_@]', '_', base_name)
        
    # Append ID to ensure uniqueness even if names overlap
    node_id_val = node_data.get("id")
    
    # Handle inlined nodes (e.g. "Sub_1@@4")
    if isinstance(node_id_val, str) and "@@" in node_id_val:
        try:
            # The ID is "ParentLogicalID@@NumericID"
            pfx, raw_id = node_id_val.rsplit("@@", 1)
            # Rebuild as "ParentLogicalID@@ChildLogicalName_NumericID"
            return f"{pfx}@@{base_name}_{raw_id}"
        except: pass
        
    # Final assembly: Use _ as separator for individual node IDs 
    # (keeps hierarchical segments distinct when using @@ as path separator)
    return f"{base_name}_{node_id_val}"


def flatten_graph_json(graph_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively expands subgraphs that have the 'inline' property set to True.
    This effectively 'flattens' the graph while preserving path-based node IDs (e.g. Sub_1@@Task_5).
    """
    nodes = graph_json.get("nodes", [])
    links = graph_json.get("links", [])
    
    # 1. Check if any inlining is needed at this level
    has_inline = any(n.get("type") == "langgraph/subgraph" and n.get("properties", {}).get("inline") is True for n in nodes)
    if not has_inline:
        return graph_json
        
    final_nodes = []
    final_links = []
    
    # Track which IDs are being replaced to bridge links
    subgraph_proxies = {} # id -> { pfx: str, start_ids: list, end_ids: list }

    for n in nodes:
        props = n.get("properties", {})
        if n.get("type") == "langgraph/subgraph" and props.get("inline") is True:
            sub_name = props.get("subgraph")
            if sub_name.startswith("module@@"):
                sub_file = resolve_module_graph_path(sub_name) or resolve_module_graph_path(f"{sub_name}.json")
                if sub_file is None:
                    sub_file = Path("/dev/null")  # will trigger "not found" below
            else:
                sub_file = GRAPH_SAVE_PATH / sub_name
                if not sub_file.exists() and not sub_name.endswith(".json"):
                    sub_file = GRAPH_SAVE_PATH / f"{sub_name}.json"
            
            if not sub_file.exists():
                logger.warning("Inlined subgraph file not found: %s", sub_name)
                final_nodes.append(n)
                continue
                
            try:
                with open(sub_file, "r", encoding="utf-8") as f:
                    sub_json = json.load(f)
                
                # Recursively flatten the subgraph first
                sub_json = flatten_graph_json(sub_json)
                
                # prefix: "SubName_ID@@"
                pfx = get_node_id(n) + "@@"
                
                snodes = sub_json.get("nodes", [])
                slinks = sub_json.get("links", [])
                
                start_ids = []
                end_ids = []
                
                # Add all internal nodes with prefix
                for sn in snodes:
                    new_sn = copy.deepcopy(sn)
                    new_sn["id"] = f"{pfx}{sn['id']}"
                    
                    # Prefix portal tags to prevent cross-subgraph collisions
                    if sn.get("type") in ("langgraph/port_in", "langgraph/port_out"):
                        props = new_sn.get("properties", {})
                        if "tag" in props:
                            props["tag"] = f"{pfx}{props['tag']}"
                            
                    # Prefix node references in properties if they exist
                    # (e.g. SubgraphNodeCompletionRouter's subgraph_node_id)
                    props = new_sn.get("properties", {})
                    for key in ["subgraph_node_id", "target_node_id", "source_node_id"]:
                        if key in props and props[key] is not None:
                            try:
                                # Only prefix if it's a numeric ID (logical IDs are already handled)
                                int(props[key])
                                props[key] = f"{pfx}{props[key]}"
                            except (ValueError, TypeError):
                                pass

                    # Only collect START/END nodes that belong to THIS subgraph level.
                    # Nodes from recursively inlined sub-subgraphs already have '@@' in
                    # their original ID and should NOT be treated as entry/exit points
                    # for the current subgraph's parent bridging.
                    original_id = str(sn.get("id", ""))
                    is_top_level = "@@" not in original_id
                    if sn.get("type") == "langgraph/start" and is_top_level:
                        start_ids.append(new_sn["id"])
                    elif sn.get("type") == "langgraph/end" and is_top_level:
                        end_ids.append(new_sn["id"])
                        
                    final_nodes.append(new_sn)
                    
                # Add all internal links with prefix
                for sl in slinks:
                    new_sl = list(sl)
                    new_sl[0] = f"{pfx}{sl[0]}" # Link ID
                    new_sl[1] = f"{pfx}{sl[1]}" # Source ID
                    new_sl[3] = f"{pfx}{sl[3]}" # Target ID
                    final_links.append(new_sl)
                    
                subgraph_proxies[n["id"]] = {
                    "pfx": pfx,
                    "start_ids": start_ids,
                    "end_ids": end_ids
                }
            except Exception as e:
                logger.error("Failed to inline subgraph %s: %s", sub_name, e)
                final_nodes.append(n) # Fallback to original
        else:
            final_nodes.append(n)
            
    # Bridge parent links
    for l in links:
        # [id, src, src_slot, tgt, tgt_slot, type]
        src_id, tgt_id = l[1], l[3]
        
        # If target IS an inlined subgraph
        if tgt_id in subgraph_proxies:
            proxy = subgraph_proxies[tgt_id]
            if proxy["start_ids"]:
                # Connect to all internal start nodes
                for sid in proxy["start_ids"]:
                    new_l = list(l)
                    # Unique link ID to avoid collisions
                    new_l[0] = f"{new_l[0]}_{sid}"
                    new_l[3] = sid
                    final_links.append(new_l)
            # Original link is consumed
            continue
            
        # If source IS an inlined subgraph
        if src_id in subgraph_proxies:
            proxy = subgraph_proxies[src_id]
            if proxy["end_ids"]:
                # Connect FROM all internal end nodes
                for eid in proxy["end_ids"]:
                    new_l = list(l)
                    new_l[0] = f"{new_l[0]}_{eid}"
                    new_l[1] = eid
                    final_links.append(new_l)
            continue
            
        final_links.append(l)
        
    return {**graph_json, "nodes": final_nodes, "links": final_links}



def extract_edges(graph_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extracts all logical edges from a LiteGraph JSON, resolving Portals.
    Returns a list of dicts: [{'source': str, 'target': str, 'branch': str}, ...]
    """
    nodes = graph_json.get("nodes", [])
    links = graph_json.get("links", [])
    node_map = {n["id"]: n for n in nodes}
    
    # helper for portal resolution
    portal_entrances = defaultdict(list)
    portal_exits = defaultdict(list)
    direct_transitions = []
    
    for link in links:
        # Link Format: [id, origin_id, origin_slot, target_id, target_slot, type]
        src_id, tgt_id = link[1], link[3]
        src_slot_idx = link[2]
        
        src_node, tgt_node = node_map.get(src_id), node_map.get(tgt_id)
        if not src_node or not tgt_node:
            continue
            
        src_type = src_node.get("type")
        tgt_type = tgt_node.get("type")

        # Resolve Branch Name for the source
        branch_name = "default"
        outputs = src_node.get("outputs", [])
        if src_slot_idx < len(outputs):
            branch_name = outputs[src_slot_idx].get("name") or str(src_slot_idx)
        else:
            branch_name = str(src_slot_idx)

        # Handle Portal Logic
        if tgt_type == "langgraph/port_in":
            tag = tgt_node.get("properties", {}).get("tag", "A")
            src_name = get_node_id(src_node)
            portal_entrances[tag].append((src_name, branch_name))
            continue 

        if src_type == "langgraph/port_out":
            tag = src_node.get("properties", {}).get("tag", "A")
            tgt_name = get_node_id(tgt_node)
            portal_exits[tag].append(tgt_name)
            continue

        direct_transitions.append({
            "source": get_node_id(src_node),
            "target": get_node_id(tgt_node),
            "branch": branch_name
        })

    # Join Portals into Transitions
    all_transitions = direct_transitions
    for tag, sources in portal_entrances.items():
        targets = portal_exits.get(tag, [])
        for src_name, branch in sources:
            for tgt_name in targets:
                all_transitions.append({
                    "source": src_name,
                    "target": tgt_name,
                    "branch": branch
                })
    return all_transitions


def extract_interrupts(graph_json: Dict[str, Any]) -> List[str]:
    """
    Scans the LiteGraph JSON for nodes that have properties.interrupt_before = True.
    Returns a list of unique node IDs (sanitized for LangGraph).
    Flattens subgraphs first so inlined children are included instead of the
    subgraph proxy (which won't exist in the compiled graph).
    """
    flat_json = flatten_graph_json(graph_json)
    nodes = flat_json.get("nodes", [])
    interrupt_ids = []

    for node_data in nodes:
        props = node_data.get("properties", {})
        if props.get("interrupt_before") is True:
            u_id = get_node_id(node_data)
            if u_id:
                interrupt_ids.append(u_id)

    return interrupt_ids


def extract_all_node_ids(graph_json: Dict[str, Any]) -> List[str]:
    """
    Returns node IDs for all real computation nodes in the graph.
    Used by debug mode to build a complete interrupt_before list.
    Flattens subgraphs first so inlined children are included instead of the
    subgraph proxy (which won't exist in the compiled graph).

    Must skip the same types that _internal_build_langgraph excludes:
    - reserved_types (port_in, port_out, state) are never added to the workflow
    - start/end are trivial passthroughs, not worth pausing at
    - WithNamespace is a routing passthrough, not a computation node
    - subgraph proxies are replaced by their inlined children
    """
    flat_json = flatten_graph_json(graph_json)
    nodes = flat_json.get("nodes", [])
    node_ids = []
    skip_types = {
        "langgraph/start",
        "langgraph/end",
        "langgraph/port_in",
        "langgraph/port_out",
        "langgraph/state",
        "langgraph/WithNamespace",
        "langgraph/subgraph",
    }
    for node_data in nodes:
        if node_data.get("type") in skip_types:
            continue
        u_id = get_node_id(node_data)
        if u_id:
            node_ids.append(u_id)
    return node_ids

# Internal stack to detect circular subgraph references during build
_BUILDING_STACK = set()

def build_langgraph_from_json(graph_json: Dict[str, Any], node_registry: Dict[str, Any], graph_id: Optional[str] = None) -> StateGraph:
    """
    Builds a LangGraph StateGraph from a LiteGraph JSON export.
    Supports both direct body connections and the Legacy Bridge Architecture (Ports).
    
    Uses Annotated[dict, merge_dicts] as state schema to support:
    - Dynamic keys (any key can be added to state)
    - Fan-in patterns (multiple parallel branches can update state simultaneously)
    """
    if graph_id:
        if graph_id in _BUILDING_STACK:
            raise ValueError(f"Circular subgraph reference detected: '{graph_id}' references itself.")
        _BUILDING_STACK.add(graph_id)

    # Automatically flatten any subgraphs marked as 'inline'
    graph_json = flatten_graph_json(graph_json)

    try:
        return _internal_build_langgraph(graph_json, node_registry, graph_id=graph_id)
    finally:
        if graph_id:
            _BUILDING_STACK.remove(graph_id)

def _internal_build_langgraph(graph_json: Dict[str, Any], node_registry: Dict[str, Any], graph_id: Optional[str] = None) -> StateGraph:
    """
    Builds a LangGraph StateGraph from a LiteGraph JSON export.
    Supports both direct body connections and the Legacy Bridge Architecture (Ports).
    
    Uses Annotated[dict, merge_dicts] as state schema to support:
    - Dynamic keys (any key can be added to state)
    - Fan-in patterns (multiple parallel branches can update state simultaneously)
    """
    # Use Annotated dict with merge_dicts reducer for flexible state with fan-in support
    workflow = StateGraph(Annotated[dict, merge_dicts])
    
    nodes = graph_json.get("nodes", [])
    reserved_types = ["langgraph/port_in", "langgraph/port_out", "langgraph/state"]

    # 1. Extract Transitions & Resolve Portals (Needed for static analysis)
    all_transitions = extract_edges(graph_json)

    # 2. NAMESPACE PROPAGATION (Static Analysis)
    # Trace which nodes should run in a specific namespace based on WithNamespace nodes.
    # Logic: START -> WithNamespace(A) -> Node1 -- then Node1 inherits namespace A.
    node_namespaces = {} # unique_id -> namespace_name
    
    # helper to find neighbors
    adj = defaultdict(list)
    for trans in all_transitions:
        adj[trans["source"]].append(trans["target"])

    # BFS from START nodes to propagate namespace
    start_nodes_list = [get_node_id(n) for n in nodes if n.get("type") == "langgraph/start"]
    queue = [(s, None) for s in start_nodes_list]
    visited_ns = set()

    # Identify WithNamespace and END nodes
    with_ns_props = {}
    end_node_ids = set()
    for n in nodes:
        u_id = get_node_id(n)
        if n.get("type") == "langgraph/WithNamespace":
            with_ns_props[u_id] = n.get("properties", {}).get("namespace")
        elif n.get("type") == "langgraph/end":
            end_node_ids.add(u_id)

    while queue:
        curr_id, curr_ns = queue.pop(0)
        if (curr_id, curr_ns) in visited_ns: continue
        visited_ns.add((curr_id, curr_ns))

        # Determine NS to pass to children
        if curr_id in with_ns_props:
            # If the node is a WithNamespace node, its value (even if empty) overrides the parent
            new_val = with_ns_props[curr_id]
            # Normalize "" or explicit "global" to None to indicate root state
            next_ns = None if not new_val or new_val == "global" else new_val
        else:
            # Inherit from parent
            next_ns = curr_ns

        for neighbor in adj.get(curr_id, []):
            logger.debug("BFS: %s (%s) -> %s", curr_id, curr_ns, neighbor)
            # Only root-level END nodes should stay global.
            # Subgraph-internal END nodes (which have @@ in ID) should inherit namespace.
            if neighbor in end_node_ids and "@@" not in neighbor:
                node_namespaces[neighbor] = None
                continue

            if neighbor not in node_namespaces:
                node_namespaces[neighbor] = next_ns
                queue.append((neighbor, next_ns))
            elif node_namespaces[neighbor] != next_ns:
                # If one is None (global), the specific namespace wins
                if node_namespaces[neighbor] is None:
                    logger.debug("Namespace '%s' overriding global at node %s", next_ns, neighbor)
                    node_namespaces[neighbor] = next_ns
                    queue.append((neighbor, next_ns))
                elif next_ns is None:
                    # Existing specific namespace remains, ignore incoming global
                    continue
                else:
                    # Both are specific and different -> Conflicting isolation
                    logger.error("NAMESPACE CONFLICT at node %s: existing='%s', incoming='%s' (Path: %s -> %s)", 
                                 neighbor, node_namespaces[neighbor], next_ns, curr_id, neighbor)
                    raise ValueError(
                        f"Ambiguous namespace for node '{neighbor}': "
                        f"It is reached via multiple conflicting namespaces ('{node_namespaces[neighbor]}' and '{next_ns}'). "
                        "Each node must belong to exactly one namespace."
                    )

    # Log final namespace map for debugging
    logger.debug("Final Node Namespace Map for graph '%s':", graph_id or "root")
    for nid, ns in sorted(node_namespaces.items()):
        if ns:
            logger.debug("  %s -> %s", nid, ns)

    # Define standard retry policy for RetriableError
    standard_retry_policy = RetryPolicy(
        retry_on=RetriableError,
        initial_interval=10.0,
        backoff_factor=2.0,
        max_interval=120.0,
        max_attempts=5,
    )

    # 3. Add Nodes (Worker Bodies)
    added_nodes = set()
    router_instances = {} # unique_id -> RouterNode instance
    
    from langchain_core.runnables import RunnableConfig
    
    def passthrough_node(state: Dict[str, Any], config: RunnableConfig = None) -> Dict[str, Any]:
        """A simple passthrough node that ensures thread_id is in the state."""
        # Ensure thread_id from LangGraph config is copied into the state
        if config and "configurable" in config:
            thread_id = config["configurable"].get("thread_id")
            if thread_id and "thread_id" not in state:
                logger.debug("Injecting thread_id %s into state via START node", thread_id)
                return {**state, "thread_id": thread_id}
                
        return state

    for node_data in nodes:
        node_type = node_data.get("type")
        if node_type in reserved_types:
            continue
            
        unique_id = get_node_id(node_data)
        logger.debug("Processing node: %s (Type: %s)", unique_id, node_type)
        if unique_id in added_nodes:
            continue

        # Get props early so they are available for all handlers
        props = node_data.get("properties", {})
        node_fn = None
        node_obj = None # RESET node_obj for each iteration to avoid leaks

        if node_type == "langgraph/start":
            node_fn = passthrough_node
            # Only connect root-level START nodes to LangGraph's global START.
            # Inlined subgraph START nodes (ID contains '@@') are reached via
            # the flattened parent links, not from the global entry point.
            if "@@" not in unique_id:
                workflow.add_edge(START, unique_id)

        elif node_type == "langgraph/WithNamespace":
            # WithNamespace is a system node. At runtime, it's just a passthrough.
            # The BFS already used it to calculate node namespaces during compilation.
            node_fn = passthrough_node

        elif node_type == "langgraph/end":
            node_fn = passthrough_node
            # Only connect root-level END nodes to LangGraph's global END.
            # Inlined subgraph END nodes are reached via flattened parent links.
            if "@@" not in unique_id:
                workflow.add_edge(unique_id, END)
            
        elif node_type == "langgraph/subgraph":
            subgraph_name = props.get("subgraph")
            if not subgraph_name:
                node_fn = passthrough_node
            else:
                try:
                    if subgraph_name.startswith("module@@"):
                        subgraph_file = resolve_module_graph_path(subgraph_name) or resolve_module_graph_path(f"{subgraph_name}.json")
                        if subgraph_file is None:
                            subgraph_file = Path("/dev/null")  # will trigger "not found" below
                    else:
                        subgraph_file = GRAPH_SAVE_PATH / subgraph_name
                        if not subgraph_file.exists() and not subgraph_name.endswith(".json"):
                            subgraph_file = GRAPH_SAVE_PATH / f"{subgraph_name}.json"
                    
                    if not subgraph_file.exists():
                        logger.warning("Subgraph file not found: %s", subgraph_file)
                        node_fn = passthrough_node
                    else:
                        with open(subgraph_file, "r", encoding="utf-8") as f:
                            subgraph_json = json.load(f)
                        
                        # Build fresh subgraph instance (Ensures node isolation)
                        subgraph_workflow = build_langgraph_from_json(subgraph_json, node_registry, graph_id=subgraph_name)
                        node_obj = subgraph_workflow.compile()
                        node_fn = node_obj
                except Exception as e:
                    logger.error("Error loading subgraph %s: %s", subgraph_name, e)
                    node_fn = passthrough_node
        else:
            # Registry Node Lookup
            registry_key = props.get("name") or node_data.get("title") or node_type.split('/')[-1]
            node_item = node_registry.get(registry_key) or node_registry.get(node_type)
            
            import inspect
            if inspect.isclass(node_item):
                try:
                    node_obj = node_item(**props)
                except Exception as e:
                    logger.error("Failed to instantiate node %s: %s", unique_id, e)
                    raise ValueError(f"Failed to instantiate node {unique_id}: {e}") from e
            else:
                node_obj = node_item

            node_fn = node_obj.run if hasattr(node_obj, "run") else node_obj
            if isinstance(node_obj, RouterNode):
                router_instances[unique_id] = node_obj

            if not node_fn:
                def identity(x, name=unique_id): return x
                node_fn = identity
            
        # ---------------------------------------------------------------------
        # APPLY NAMESPACE WRAPPING / BOXING
        # ---------------------------------------------------------------------
        assigned_ns = node_namespaces.get(unique_id)
        if assigned_ns:
            from src.nodes.abstract.base_node import BaseNode
            is_base_node = isinstance(node_obj, BaseNode)

            def create_namespaced_wrapper(ns, original_run, isolation_mode="boxing"):
                async def wrapped(state):
                    from src.utils.setup.logger import namespace_scope
                    state_key = f"{ns}@@namespace"
                    
                    if isolation_mode == "injection":
                        # Standard Injection: For BaseNodes that handle their own isolation internally
                        local_state = {**state, "@@namespace": ns}
                        res = original_run(local_state)
                        if inspect.isawaitable(res):
                            return await res
                        return res
                    else:
                        # BOXING: Isolate functions/passthroughs/subgraphs to only see their namespace slice
                        scoped_state = state.get(state_key, {})
                        global_state = {k: v for k, v in state.items() if not k.endswith("@@namespace") and k != "@@namespace"}
                        local_input = {**global_state, **scoped_state}
                        
                        with namespace_scope(ns):
                            # Handle Subgraphs (Runnable) vs Functions
                            if hasattr(original_run, "ainvoke"):
                                result = await original_run.ainvoke(local_input)
                            elif hasattr(original_run, "invoke"):
                                import asyncio
                                result = await asyncio.to_thread(original_run.invoke, local_input)
                            else:
                                if inspect.iscoroutinefunction(original_run):
                                    result = await original_run(local_input)
                                else:
                                    import asyncio
                                    result = await asyncio.to_thread(original_run, local_input)
                        
                        # Box back results into the namespace bucket
                        boxed_result = {k: v for k, v in result.items() if k not in global_state}
                        return {state_key: boxed_result}
                return wrapped

            mode = "injection" if is_base_node else "boxing"
            logger.debug("Auto-Namespacing node: %s -> Namespace: %s (Mode: %s)", unique_id, assigned_ns, mode)
            node_fn = create_namespaced_wrapper(assigned_ns, node_fn, mode)

        # Apply retries only to functional nodes (not special control nodes)
        if node_type not in ["langgraph/start", "langgraph/end", "langgraph/WithNamespace"]:
            workflow.add_node(unique_id, node_fn, retry=standard_retry_policy)
        else:
            workflow.add_node(unique_id, node_fn)

        added_nodes.add(unique_id)

    start_node_ids = {get_node_id(n) for n in nodes if n.get("type") == "langgraph/start"}
    edges_by_source = defaultdict(lambda: defaultdict(list))

    for trans in all_transitions:
        src, tgt, branch = trans["source"], trans["target"], trans["branch"]
        edges_by_source[src][branch].append(tgt)
            
    # Apply edges
    for src, branch_map in edges_by_source.items():
        if src in router_instances:
            # Routing: Use conditional edges driven by the RouterNode's logic
            node_instance = router_instances[src]
            
            def create_router(node_obj, available_branches, ns=None):
                async def router(state):
                    # 1. Try to get the route calculated by the node's internal logic
                    # If namespaced, check the namespace bucket first
                    decision = None
                    if ns:
                        state_key = f"{ns}@@namespace"
                        ns_bucket = state.get(state_key, {})
                        decision = ns_bucket.get("next_step")

                    if not decision:
                        decision = state.get("next_step")
                        
                    if not decision:
                        res = node_obj.get_route(state)
                        if inspect.isawaitable(res):
                            decision = await res
                        else:
                            decision = res
                    return decision
                return router
                
            path_map = {b: tgts[0] for b, tgts in branch_map.items()}
            workflow.add_conditional_edges(src, create_router(node_instance, branch_map, ns=node_namespaces.get(src)), path_map)
        else:
            # Fan-out: Every connected path is taken in parallel
            for tgts in branch_map.values():
                for tgt in tgts:
                    workflow.add_edge(src, tgt)

    return workflow

if __name__ == "__main__":
    file_path = GRAPH_SAVE_PATH / "test_parent_graph.json"
    if not file_path.exists():
        print(f"File not found: {file_path}")
    else:
        with open(file_path, "r") as f:
            data = json.load(f)
            edges = extract_edges(data)
            print(f"Edges in {file_path.name}:")
            for edge in edges:
                print(f"  {edge['source']} --({edge['branch']})--> {edge['target']}")
