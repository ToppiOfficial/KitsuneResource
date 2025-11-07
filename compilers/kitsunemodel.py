import re
from typing import Any, List, Dict, Optional

class KitsuneNode:
    """Represents a node in the KitsuneValue format"""
    
    def __init__(self, **kwargs):
        self.attributes = {}
        self.children = []
        
        for key, value in kwargs.items():
            if key == 'children':
                self.children = value if isinstance(value, list) else [value]
            else:
                self.attributes[key] = value
    
    def add_child(self, child: 'KitsuneNode'):
        """Add a child node"""
        self.children.append(child)
    
    def get(self, key: str, default=None):
        """Get an attribute value"""
        return self.attributes.get(key, default)
    
    def set(self, key: str, value: Any):
        """Set an attribute value"""
        self.attributes[key] = value
    
    def find(self, **kwargs) -> Optional['KitsuneNode']:
        """Find first child node matching the given attributes"""
        for child in self.children:
            if all(child.get(key) == value for key, value in kwargs.items()):
                return child
        return None
    
    def find_all(self, **kwargs) -> List['KitsuneNode']:
        """Find all child nodes matching the given attributes"""
        results = []
        for child in self.children:
            if all(child.get(key) == value for key, value in kwargs.items()):
                results.append(child)
        return results
    
    def find_all_recursive(self, **kwargs) -> List['KitsuneNode']:
        """Recursively find all nodes matching the given attributes (depth-first, top-to-bottom)"""
        results = []
        if all(self.get(key) == value for key, value in kwargs.items()):
            results.append(self)
        for child in self.children:
            results.extend(child.find_all_recursive(**kwargs))
        return results
    
    def find_recursive(self, **kwargs) -> Optional['KitsuneNode']:
        """Recursively find first node matching the given attributes"""
        if all(self.get(key) == value for key, value in kwargs.items()):
            return self
        for child in self.children:
            result = child.find_recursive(**kwargs)
            if result:
                return result
        return None
    
    def __repr__(self):
        return f"KitsuneNode({self.attributes}, children={len(self.children)})"


class KitsuneValue:
    """Parser and exporter for KitsuneValue format"""
    
    def __init__(self):
        self.metadata = {}
        self.root_nodes = []
    
    def find(self, **kwargs) -> Optional[KitsuneNode]:
        """Find first root node matching the given attributes"""
        for node in self.root_nodes:
            if all(node.get(key) == value for key, value in kwargs.items()):
                return node
        return None
    
    def find_all(self, **kwargs) -> List[KitsuneNode]:
        """Find all root nodes matching the given attributes"""
        results = []
        for node in self.root_nodes:
            if all(node.get(key) == value for key, value in kwargs.items()):
                results.append(node)
        return results
    
    def find_all_recursive(self, **kwargs) -> List[KitsuneNode]:
        """Recursively search all nodes and return all matches (depth-first, top-to-bottom)"""
        results = []
        for node in self.root_nodes:
            results.extend(node.find_all_recursive(**kwargs))
        return results
    
    def get_metadata(self, key: str, default=None) -> Any:
        """Get a specific metadata value"""
        return self.metadata.get(key, default)
    
    def set_metadata(self, key: str, value: Any):
        """Set a metadata value"""
        self.metadata[key] = value
    
    def get_metadata_header(self) -> str:
        """Get the metadata header as a string"""
        if not self.metadata:
            return ""
        meta_parts = [f"{k}:{v}" for k, v in self.metadata.items()]
        return f"<!--{' '.join(meta_parts)}-->"
    
    def merge(self, other: 'KitsuneValue', overwrite_metadata: bool = False):
        """
        Merge another KitsuneValue into this one.
        - Merges metadata (other's metadata takes priority if overwrite_metadata=True)
        - Merges nodes intelligently: matching nodes by _class are merged recursively
        - Non-matching nodes from 'other' are appended
        """
        # Merge metadata
        if overwrite_metadata:
            self.metadata.update(other.metadata)
        else:
            for key, value in other.metadata.items():
                if key not in self.metadata:
                    self.metadata[key] = value
        
        # Merge root nodes
        for other_node in other.root_nodes:
            self._merge_node_into_list(self.root_nodes, other_node)
    
    def _merge_node_into_list(self, target_list: List[KitsuneNode], new_node: KitsuneNode):
        """
        Merge a node into a list of nodes.
        If a matching node exists (same _class and key attributes), merge them.
        Otherwise, append the new node.
        """
        # Try to find a matching node in target list
        match = None
        for existing_node in target_list:
            if self._nodes_match(existing_node, new_node):
                match = existing_node
                break
        
        if match:
            # Merge into existing node
            self._merge_nodes(match, new_node)
        else:
            # No match found, append as new node
            target_list.append(new_node)
    
    def _nodes_match(self, node1: KitsuneNode, node2: KitsuneNode) -> bool:
        """
        Check if two nodes should be considered the same node for merging.
        Nodes match if they have the same _class and name (if name exists).
        For nodes without names, they must match on all attributes to be considered duplicates.
        """
        class1 = node1.get('_class')
        class2 = node2.get('_class')
        
        if class1 != class2:
            return False
        
        # If both have names, they must match
        name1 = node1.get('name')
        name2 = node2.get('name')
        
        if name1 is not None and name2 is not None:
            return name1 == name2
        
        # One has name, one doesn't - don't match
        if name1 is not None or name2 is not None:
            return False
        
        # Neither has a name - check if ALL attributes match (exact duplicate)
        # This prevents merging different nodes of the same type
        if set(node1.attributes.keys()) != set(node2.attributes.keys()):
            return False
        
        for key in node1.attributes.keys():
            if node1.get(key) != node2.get(key):
                return False
        
        return True
    
    def _merge_nodes(self, target: KitsuneNode, source: KitsuneNode):
        """
        Merge source node into target node.
        - Updates attributes from source (source takes priority)
        - Merges children recursively
        """
        # Merge attributes (source overwrites target)
        for key, value in source.attributes.items():
            target.attributes[key] = value
        
        # Merge children
        for source_child in source.children:
            self._merge_node_into_list(target.children, source_child)
    
    def find_recursive(self, **kwargs) -> Optional[KitsuneNode]:
        """Recursively search all nodes for first match"""
        for node in self.root_nodes:
            result = node.find_recursive(**kwargs)
            if result:
                return result
        return None
    
    def parse(self, text: str) -> List[KitsuneNode]:
        """Parse KitsuneValue format text into node structure"""
        # Extract metadata from header comment
        header_match = re.match(r'<!--(.+?)-->', text.strip())
        if header_match:
            self._parse_metadata(header_match.group(1))
            text = text[header_match.end():].strip()
        
        # Remove outer braces
        text = text.strip()
        if text.startswith('{') and text.endswith('}'):
            text = text[1:-1].strip()
        
        self.root_nodes = self._parse_nodes(text)
        return self.root_nodes
    
    def _parse_metadata(self, meta_text: str):
        """Parse metadata from header comment"""
        parts = meta_text.split()
        for part in parts:
            if ':' in part:
                key, value = part.split(':', 1)
                self.metadata[key.strip()] = value.strip()
    
    def _parse_nodes(self, text: str) -> List[KitsuneNode]:
        """Parse multiple nodes at the same level"""
        nodes = []
        i = 0
        
        while i < len(text):
            # Skip whitespace and commas
            while i < len(text) and text[i] in ' \t\n\r,':
                i += 1
            
            if i >= len(text):
                break
            
            # Find node start
            if text[i] == '{':
                node, end_idx = self._parse_single_node(text, i)
                if node:
                    nodes.append(node)
                i = end_idx
            else:
                i += 1
        
        return nodes
    
    def _parse_single_node(self, text: str, start: int) -> tuple:
        """Parse a single node starting at given index"""
        if text[start] != '{':
            return None, start
        
        i = start + 1
        node_data = {}
        children_list = []
        
        while i < len(text):
            # Skip whitespace
            while i < len(text) and text[i] in ' \t\n\r':
                i += 1
            
            if i >= len(text):
                break
            
            # End of node
            if text[i] == '}':
                node = KitsuneNode(**node_data)
                node.children = children_list
                return node, i + 1
            
            # Skip commas
            if text[i] == ',':
                i += 1
                continue
            
            # Parse key-value pair
            key_match = re.match(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*:', text[i:])
            if key_match:
                key = key_match.group(1)
                i += key_match.end()
                
                # Skip whitespace after colon
                while i < len(text) and text[i] in ' \t\n\r':
                    i += 1
                
                # Parse value
                if text[i] == '[':
                    # Array/children
                    value, i = self._parse_array(text, i)
                    if key == 'children':
                        children_list = value
                    else:
                        node_data[key] = value
                elif text[i] == '{':
                    # Nested object
                    value, i = self._parse_single_node(text, i)
                    node_data[key] = value
                else:
                    # Simple value
                    value, i = self._parse_value(text, i)
                    node_data[key] = value
            else:
                i += 1
        
        node = KitsuneNode(**node_data)
        node.children = children_list
        return node, i
    
    def _parse_array(self, text: str, start: int) -> tuple:
        """Parse an array starting at given index"""
        if text[start] != '[':
            return [], start
        
        i = start + 1
        items = []
        
        while i < len(text):
            # Skip whitespace
            while i < len(text) and text[i] in ' \t\n\r':
                i += 1
            
            if i >= len(text):
                break
            
            # End of array
            if text[i] == ']':
                return items, i + 1
            
            # Skip commas
            if text[i] == ',':
                i += 1
                continue
            
            # Parse array item
            if text[i] == '{':
                item, i = self._parse_single_node(text, i)
                items.append(item)
            elif text[i] == '[':
                # Nested array
                item, i = self._parse_array(text, i)
                items.append(item)
            else:
                item, i = self._parse_value(text, i)
                items.append(item)
        
        return items, i
    
    def _parse_value(self, text: str, start: int) -> tuple:
        """Parse a simple value (string, number, boolean, None)"""
        i = start
        
        # Skip whitespace
        while i < len(text) and text[i] in ' \t\n\r':
            i += 1
        
        # String with quotes
        if text[i] in '"\'':
            quote = text[i]
            i += 1
            value_start = i
            while i < len(text) and text[i] != quote:
                if text[i] == '\\':
                    i += 2
                else:
                    i += 1
            value = text[value_start:i]
            return value, i + 1
        
        # Other values
        value_start = i
        while i < len(text) and text[i] not in ',\n\r}]':
            i += 1
        
        value_str = text[value_start:i].strip()
        
        # Convert to appropriate type
        if value_str == 'None' or value_str == 'null':
            return None, i
        elif value_str == 'True' or value_str == 'true':
            return True, i
        elif value_str == 'False' or value_str == 'false':
            return False, i
        elif value_str.replace('.', '').replace('-', '').isdigit():
            return float(value_str) if '.' in value_str else int(value_str), i
        else:
            return value_str, i
    
    def export(self, nodes: Optional[List[KitsuneNode]] = None, 
               metadata: Optional[Dict] = None, indent: int = 4) -> str:
        """Export node structure to KitsuneValue format"""
        if nodes is None:
            nodes = self.root_nodes
        if metadata is None:
            metadata = self.metadata
        
        output = []
        
        # Add metadata header
        if metadata:
            meta_parts = [f"{k}:{v}" for k, v in metadata.items()]
            output.append(f"<!--{' '.join(meta_parts)}-->\n")
        
        # Export nodes
        output.append("{\n")
        
        for idx, node in enumerate(nodes):
            output.append(self._export_node(node, 1, indent))
            if idx < len(nodes) - 1:
                output.append(",\n")
        
        output.append("\n}")
        
        return ''.join(output)
    
    def _export_node(self, node: KitsuneNode, level: int, indent: int) -> str:
        """Export a single node"""
        ind = ' ' * (indent * level)
        output = [f"{ind}{{"]
        
        items = []
        
        # Export attributes
        for key, value in node.attributes.items():
            items.append(f"\n{ind}{' ' * indent}{key}: {self._export_value(value, level + 1, indent)}")
        
        # Export children
        if node.children:
            items.append(f"\n{ind}{' ' * indent}children:")
            items.append(f"\n{ind}{' ' * indent}[")
            for idx, child in enumerate(node.children):
                items.append(f"\n{self._export_node(child, level + 2, indent)}")
                if idx < len(node.children) - 1:
                    items.append(",")
            items.append(f"\n{ind}{' ' * indent}]")
        
        output.extend(items)
        output.append(f"\n{ind}}}")
        
        return ''.join(output)
    
    def _export_value(self, value: Any, level: int, indent: int) -> str:
        """Export a value"""
        if value is None:
            return "None"
        elif isinstance(value, bool):
            return str(value)
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            if ' ' in value or ':' in value:
                return f'"{value}"'
            return value
        elif isinstance(value, KitsuneNode):
            return self._export_node(value, level, indent)
        elif isinstance(value, dict):
            # Export inline dict
            items = [f"{k}: {self._export_value(v, level, indent)}" for k, v in value.items()]
            return f"{{{', '.join(items)}}}"
        elif isinstance(value, list):
            ind = ' ' * (indent * level)
            # Check if it's a simple list (all primitives)
            if all(isinstance(v, (int, float, str, bool, type(None))) for v in value):
                items = [self._export_value(v, level, indent) for v in value]
                return f"[{', '.join(items)}]"
            else:
                # Complex list with nested structures
                items = [f"\n{ind}{' ' * indent}{self._export_value(v, level + 1, indent)}" 
                        for v in value]
                return f"[{''.join(items)}\n{ind}]"
        else:
            return str(value)