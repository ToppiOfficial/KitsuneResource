# KitsuneValue Library Documentation

A Python library for parsing, creating, and exporting hierarchical node-based data in a JSON-like format with metadata headers.

## Table of Contents
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Core Classes](#core-classes)
- [Creating Nodes](#creating-nodes)
- [Parsing Files](#parsing-files)
- [Exporting Data](#exporting-data)
- [Searching Nodes](#searching-nodes)
- [Working with Metadata](#working-with-metadata)
- [Merging Data](#merging-data)
- [Data Types](#data-types)
- [Examples](#examples)

---

## Installation

Simply copy the `KitsuneValue` library code into your project. No external dependencies required.

```python
from kitsune_parser import KitsuneValue, KitsuneNode
```

---

## Quick Start

### Creating Data
```python
from kitsune_parser import KitsuneValue, KitsuneNode

# Create a KitsuneValue instance
kv = KitsuneValue()
kv.metadata = {"format": "l4d2", "version": "1.0"}

# Create nodes
node = KitsuneNode(_class="Transform", name="player", position=[0, 0, 0])
child = KitsuneNode(_class="Mesh", rendermesh="models/player.mdl")
node.add_child(child)

kv.root_nodes = [node]

# Export to string
output = kv.export()
print(output)
```

### Parsing Data
```python
# Parse from string
kv = KitsuneValue()
kv.parse(text)

# Access nodes
transform = kv.find(_class="Transform")
print(transform.get('name'))
```

---

## Core Classes

### `KitsuneValue`
Main container class for the entire document.

**Properties:**
- `metadata` (dict): Metadata key-value pairs for the header
- `root_nodes` (list): List of top-level `KitsuneNode` objects

**Methods:** See sections below.

### `KitsuneNode`
Represents a single node with attributes and children.

**Properties:**
- `attributes` (dict): Node's key-value attributes
- `children` (list): List of child `KitsuneNode` objects

**Methods:** See sections below.

---

## Creating Nodes

### Basic Node Creation
```python
# Create node with attributes
node = KitsuneNode(_class="BodyGroup", name="body", enabled=True)

# Access attributes
class_name = node.get('_class')  # Returns "BodyGroup"
name = node.get('name', 'default')  # Returns "body" or 'default' if not found

# Set attributes
node.set('visible', True)
```

### Adding Children
```python
parent = KitsuneNode(_class="Parent")
child1 = KitsuneNode(_class="Child", id=1)
child2 = KitsuneNode(_class="Child", id=2)

# Method 1: add_child()
parent.add_child(child1)
parent.add_child(child2)

# Method 2: Pass during creation
parent = KitsuneNode(
    _class="Parent",
    children=[child1, child2]
)
```

### Complex Data Types
```python
# Matrices (arrays of arrays)
node = KitsuneNode(
    _class="Transform",
    matrix=[
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1]
    ]
)

# Nested objects
node = KitsuneNode(
    _class="Config",
    settings={"width": 1920, "height": 1080}
)

# Mixed arrays
node = KitsuneNode(
    _class="Data",
    items=[1, 2.5, "text", True, None, [1, 2, 3]]
)
```

---

## Parsing Files

### Parse from String
```python
text = """<!--format:l4d2-->
{
    {
        _class: RenderMeshList
        children: [...]
    }
}"""

kv = KitsuneValue()
kv.parse(text)
```

### Parse from File
```python
with open('model.kitsune', 'r') as f:
    text = f.read()

kv = KitsuneValue()
kv.parse(text)
```

### Accessing Parsed Data
```python
# Get root nodes
for node in kv.root_nodes:
    print(node.get('_class'))

# Get metadata
format_type = kv.get_metadata('format')
```

---

## Exporting Data

### Basic Export
```python
kv = KitsuneValue()
kv.root_nodes = [node1, node2]

# Export with default formatting (4 spaces indent)
output = kv.export()

# Export with custom indent
output = kv.export(indent=2)
```

### Export with Metadata
```python
kv.metadata = {
    "kitsunevalue": "text",
    "format": "l4d2",
    "version": "1.0"
}

output = kv.export()
# Output starts with: <!--kitsunevalue:text format:l4d2 version:1.0-->
```

### Save to File
```python
output = kv.export()
with open('output.kitsune', 'w') as f:
    f.write(output)
```

---

## Searching Nodes

### Find Single Node

#### `find(**kwargs)`
Find first matching node among **immediate children** or **root nodes**.

```python
# Search root nodes
render_node = kv.find(_class="RenderMeshList")

# Search children
body_group = render_node.find(_class="BodyGroup", name="body")
```

#### `find_recursive(**kwargs)`
Find first matching node **anywhere in the tree** (depth-first search).

```python
# Search entire tree
choice = kv.find_recursive(_class="BodygroupChoice", rendermesh="models/test.mdl")
```

### Find Multiple Nodes

#### `find_all(**kwargs)`
Find all matching nodes among **immediate children** or **root nodes**.

```python
# Find all immediate children with _class="BodyGroup"
body_groups = render_node.find_all(_class="BodyGroup")
```

#### `find_all_recursive(**kwargs)`
Find all matching nodes **anywhere in the tree** (depth-first, top-to-bottom order).

```python
# Find ALL BodygroupChoice nodes in entire document
all_choices = kv.find_all_recursive(_class="BodygroupChoice")

for choice in all_choices:
    print(choice.get('rendermesh'))
```

### Search Examples
```python
# Find by multiple attributes
node = kv.find(_class="Transform", name="player", enabled=True)

# Find by any attribute
nodes = kv.find_all(type="weapon")

# Recursive search preserves hierarchy order
all_meshes = kv.find_all_recursive(_class="Mesh")
# Returns: [mesh1, mesh2, mesh3, ...] in tree order
```

---

## Working with Metadata

### Get Metadata
```python
# Get specific value
format_type = kv.get_metadata('format')
version = kv.get_metadata('version', '1.0')  # With default

# Get full header string
header = kv.get_metadata_header()
# Returns: "<!--format:l4d2 version:1.0-->"
```

### Set Metadata
```python
# Set individual values
kv.set_metadata('format', 'l4d2')
kv.set_metadata('version', '2.0')

# Set multiple at once
kv.metadata = {
    "kitsunevalue": "text",
    "format": "l4d2",
    "version": "1.0"
}
```

### Metadata in Export
```python
kv.metadata = {"format": "l4d2", "author": "kitsune"}
output = kv.export()

# Output:
# <!--format:l4d2 author:kitsune-->
# {
#     ...
# }
```

---

## Merging Data

### Basic Merge
The `merge()` method intelligently combines two `KitsuneValue` objects.

```python
source = KitsuneValue()
source.root_nodes = [node1, node2]

new_data = KitsuneValue()
new_data.root_nodes = [node3, node4]

# Merge new_data into source
source.merge(new_data)
```

### Merge Behavior

**Node Matching:**
- Nodes with **same `_class` and `name`** are merged (children combined)
- Nodes **without names** must match on **all attributes** to be considered duplicates
- Non-matching nodes are **appended**

**Metadata Merging:**
```python
# Default: Keep existing metadata, add new keys
source.merge(new_data)

# Overwrite: new_data metadata overwrites source
source.merge(new_data, overwrite_metadata=True)
```

### Merge Example
```python
# Source has:
# RenderMeshList > BodyGroup(name="body") > Choice(rendermesh="A")

# New has:
# RenderMeshList > BodyGroup(name="body") > Choice(rendermesh="B")
#                                        > Choice(rendermesh="A")  # duplicate
#                > BodyGroup(name="head") > Choice(rendermesh="C")

source.merge(new_data)

# Result:
# RenderMeshList > BodyGroup(name="body") > Choice(rendermesh="A")
#                                        > Choice(rendermesh="B")
#                > BodyGroup(name="head") > Choice(rendermesh="C")
```

### Smart Duplicate Detection
```python
# These are considered duplicates (all attributes match)
choice1 = KitsuneNode(_class="Choice", rendermesh="A")
choice2 = KitsuneNode(_class="Choice", rendermesh="A")

# These are NOT duplicates (different attributes)
choice3 = KitsuneNode(_class="Choice", rendermesh="B")
```

---

## Data Types

### Supported Types

| Type | Example | Export Format |
|------|---------|---------------|
| String | `"text"` or `text` | `text` or `"text with spaces"` |
| Integer | `42` | `42` |
| Float | `3.14` | `3.14` |
| Boolean | `True` / `False` | `True` / `False` |
| None | `None` | `None` |
| List | `[1, 2, 3]` | `[1, 2, 3]` |
| Nested List | `[[1, 2], [3, 4]]` | `[[1, 2], [3, 4]]` |
| Dict | `{"x": 1, "y": 2}` | `{x: 1, y: 2}` |
| KitsuneNode | Node object | `{ _class: ... }` |

### Type Examples
```python
node = KitsuneNode(
    _class="Example",
    
    # Strings
    name="player",
    path="models/test.mdl",
    
    # Numbers
    health=100,
    speed=5.5,
    
    # Booleans
    enabled=True,
    visible=False,
    
    # None
    parent=None,
    
    # Arrays
    position=[0, 10, 5],
    matrix=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    
    # Mixed arrays
    data=[1, "text", True, None],
    
    # Dicts
    config={"width": 1920, "height": 1080}
)
```

### Matrix Support
```python
# 3x3 identity matrix
transform = KitsuneNode(
    _class="Transform",
    matrix=[
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1]
    ]
)

# Access matrix values
matrix = transform.get('matrix')
row1 = matrix[0]          # [1, 0, 0]
element = matrix[1][1]    # 1
```

---

## Examples

### Example 1: Game Model Definition
```python
kv = KitsuneValue()
kv.metadata = {"format": "l4d2", "version": "1.0"}

# Create body group with multiple choices
body_choice1 = KitsuneNode(
    _class="BodygroupChoice",
    rendermesh="models/survivor.mdl",
    transform=[[1, 0, 0], [0, 1, 0], [0, 0, 1]]
)

body_choice2 = KitsuneNode(
    _class="BodygroupChoice",
    rendermesh=None
)

body_group = KitsuneNode(_class="BodyGroup", name="body")
body_group.add_child(body_choice1)
body_group.add_child(body_choice2)

render_mesh = KitsuneNode(_class="RenderMeshList")
render_mesh.add_child(body_group)

kv.root_nodes = [render_mesh]

# Export
output = kv.export()
```

### Example 2: Parsing and Querying
```python
# Parse file
with open('model.kitsune', 'r') as f:
    kv = KitsuneValue()
    kv.parse(f.read())

# Get metadata
print(f"Format: {kv.get_metadata('format')}")

# Find specific nodes
render_list = kv.find(_class="RenderMeshList")
body_groups = render_list.find_all(_class="BodyGroup")

for group in body_groups:
    name = group.get('name')
    choice_count = len(group.children)
    print(f"BodyGroup '{name}' has {choice_count} choices")

# Find all choices recursively
all_choices = kv.find_all_recursive(_class="BodygroupChoice")
for choice in all_choices:
    mesh = choice.get('rendermesh')
    if mesh:
        print(f"  - {mesh}")
```

### Example 3: Merging Multiple Files
```python
# Load base model
base = KitsuneValue()
with open('base_model.kitsune', 'r') as f:
    base.parse(f.read())

# Load additional body groups
addon = KitsuneValue()
with open('addon_bodies.kitsune', 'r') as f:
    addon.parse(f.read())

# Merge addon into base
base.merge(addon)

# Save combined result
with open('merged_model.kitsune', 'w') as f:
    f.write(base.export())
```

### Example 4: Round-Trip Verification
```python
# Create data
kv = KitsuneValue()
kv.metadata = {"version": "1.0"}
kv.root_nodes = [node1, node2]

# Export
exported = kv.export()

# Parse back
kv2 = KitsuneValue()
kv2.parse(exported)

# Verify
assert kv2.get_metadata('version') == "1.0"
assert len(kv2.root_nodes) == 2

# Re-export should match
assert kv2.export() == exported
```

### Example 5: Building Complex Hierarchies
```python
kv = KitsuneValue()

# Create nested structure
root = KitsuneNode(_class="Scene", name="main")

# Add multiple levels
character = KitsuneNode(_class="Character", name="player")
body = KitsuneNode(_class="BodyPart", name="body")
mesh = KitsuneNode(_class="Mesh", file="body.mdl")

body.add_child(mesh)
character.add_child(body)
root.add_child(character)

kv.root_nodes = [root]

# Later, find deeply nested node
mesh_node = kv.find_recursive(_class="Mesh", file="body.mdl")
print(f"Found mesh: {mesh_node.get('file')}")
```

---

## Format Specification

### File Structure
```
<!--key1:value1 key2:value2-->
{
    {
        attribute1: value1
        attribute2: value2
        children:
        [
            { ... },
            { ... }
        ]
    },
    {
        ...
    }
}
```

### Syntax Rules
- **Metadata header**: `<!--key:value key2:value2-->`
- **Root container**: Outer `{ }` contains all root nodes
- **Node container**: Each node is `{ }`
- **Attributes**: `key: value` format
- **Children**: `children: [...]` array
- **Arrays**: `[item1, item2, ...]`
- **Whitespace**: Flexible, can be single-line or multi-line
- **Comments**: Only metadata header supports `<!--` syntax

### Naming Conventions
- Use `_class` for node type identification
- Use `name` for unique node identification within a type
- Custom attributes can be any valid identifier

---

## API Reference

### KitsuneValue Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `parse(text)` | `text: str` | `List[KitsuneNode]` | Parse text into nodes |
| `export(nodes, metadata, indent)` | Optional params | `str` | Export to string |
| `find(**kwargs)` | Attribute filters | `KitsuneNode` or `None` | Find first root node |
| `find_all(**kwargs)` | Attribute filters | `List[KitsuneNode]` | Find all root nodes |
| `find_recursive(**kwargs)` | Attribute filters | `KitsuneNode` or `None` | Find first in tree |
| `find_all_recursive(**kwargs)` | Attribute filters | `List[KitsuneNode]` | Find all in tree |
| `get_metadata(key, default)` | `key: str`, optional default | Any | Get metadata value |
| `set_metadata(key, value)` | `key: str`, `value: Any` | None | Set metadata value |
| `get_metadata_header()` | None | `str` | Get header string |
| `merge(other, overwrite_metadata)` | `other: KitsuneValue`, `overwrite_metadata: bool` | None | Merge another KitsuneValue |

### KitsuneNode Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `add_child(child)` | `child: KitsuneNode` | None | Add child node |
| `get(key, default)` | `key: str`, optional default | Any | Get attribute value |
| `set(key, value)` | `key: str`, `value: Any` | None | Set attribute value |
| `find(**kwargs)` | Attribute filters | `KitsuneNode` or `None` | Find first child |
| `find_all(**kwargs)` | Attribute filters | `List[KitsuneNode]` | Find all children |
| `find_recursive(**kwargs)` | Attribute filters | `KitsuneNode` or `None` | Find first in subtree |
| `find_all_recursive(**kwargs)` | Attribute filters | `List[KitsuneNode]` | Find all in subtree |

---

## Best Practices

1. **Use `_class` for node types**: Consistently use `_class` attribute to identify node types
2. **Use `name` for unique identification**: Add `name` attribute to nodes that need unique identification
3. **Prefer `find()` over indexing**: Use `find(_class="...")` instead of `root_nodes[0]`
4. **Use recursive search for deep hierarchies**: `find_all_recursive()` is your friend
5. **Validate after parsing**: Check that required nodes exist after parsing
6. **Test round-trips**: Verify export->parse->export produces identical output
7. **Keep metadata simple**: Use simple key:value pairs in metadata header
8. **Merge carefully**: Understand matching behavior before merging complex structures

---

## Troubleshooting

### Common Issues

**Problem: Nodes not merging correctly**
- Solution: Ensure nodes have `name` attributes, or all attributes match exactly

**Problem: Can't find node with `find()`**
- Solution: Use `find_recursive()` to search entire tree, not just immediate children

**Problem: Matrix values parsing as strings**
- Solution: Ensure proper array syntax `[[1, 0, 0], [0, 1, 0]]` with no quotes

**Problem: Parse fails on exported text**
- Solution: Check for special characters in string values that need quoting

**Problem: Duplicate nodes after merge**
- Solution: Verify nodes have matching `_class` and `name` attributes for deduplication

---

## License & Support

This library is provided as-is. Feel free to modify and extend for your needs.

For questions or issues, refer to the example code and test cases included in the library.