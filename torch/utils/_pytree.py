import torch
from collections import defaultdict
from typing import NamedTuple, Callable, Any, Tuple, List, Dict

"""
Contains utility functions for working with nested python data structures.

A *pytree* is Python nested data structure. It is a tree in the sense that
nodes are Python collections (e.g., list, tuple, dict) and the leaves are
Python values. Furthermore, a pytree should not contain reference cycles.

pytrees are useful for working with nested collections of Tensors. For example,
one can use `tree_map` to map a function over all Tensors inside some nested
collection of Tensors and `tree_unflatten` to get a flat list of all Tensors
inside some nested collection. pytrees are helpful for implementing nested
collection support for PyTorch APIs.

This pytree implementation is not very performant due to Python overhead
To improve the performance we can move parts of the implementation to C++.
"""

# A NodeDef holds two callables:
# - flatten_fn should take the collection and return a flat list of values.
#   It can also return some context that is used in reconstructing the
#   collection.
# - unflatten_fn should take a flat list of values and some context
#   (returned by flatten_fn). It returns the collection by reconstructing
#   it from the list and the context.
Context = Any
Collection = Any
flatten_fn_t = Callable[[Collection], Tuple[List, Context]]
unflatten_fn_t = Callable[[List, Context], Collection]
NodeDef = NamedTuple('NodeDef', [
    ('flatten_fn', flatten_fn_t),
    ('unflatten_fn', unflatten_fn_t),
])

SUPPORTED_NODES: Dict[Any, NodeDef] = {}

def _register_pytree_node(typ: Any, flatten_fn: flatten_fn_t, unflatten_fn: unflatten_fn_t):
    SUPPORTED_NODES[typ] = NodeDef(flatten_fn, unflatten_fn)

def _dict_flatten(d):
    return d.values(), list(d.keys())

def _dict_unflatten(values, context):
    return {key: value for key, value in zip(context, values)}

def _list_flatten(d):
    return d, None

def _list_unflatten(values, context):
    return list(values)

def _tuple_flatten(d):
    return list(d), None

def _tuple_unflatten(values, context):
    return tuple(values)

_register_pytree_node(dict, _dict_flatten, _dict_unflatten)
_register_pytree_node(list, _list_flatten, _list_unflatten)
_register_pytree_node(tuple, _tuple_flatten, _tuple_unflatten)


# A leaf is defined as anything that is not a Node.
def _is_leaf(pytree: 'pytree') -> bool:
    return type(pytree) not in SUPPORTED_NODES.keys()


# A TreeSpec represents the structure of a pytree. It holds:
# type: the type of root Node of the pytree
# context: some context that is useful in unflattening the pytree
# children_specs: specs for each child of the root Node
# num_leaves: the number of leaves
class TreeSpec:
    def __init__(self, typ: Any, context: Context, children_specs: List['TreeSpec']):
        self.type = typ
        self.context = context
        self.children_specs = children_specs
        self.num_leaves = sum([spec.num_leaves for spec in children_specs])

    def __repr__(self):
        return f'TreeSpec({self.type.__name__}, {self.context}, {self.children_specs})'

    def __eq__(self, other):
        return self.type == other.type and self.context == other.context \
            and self.children_specs == other.children_specs \
            and self.num_leaves == other.num_leaves

    def __ne__(self, other):
        return not self.__eq__(other)


class LeafSpec(TreeSpec):
    def __init__(self):
        super().__init__(None, None, [])
        self.num_leaves = 1

    def __repr__(self):
        return '*'


def tree_flatten(pytree: 'pytree') -> Tuple[List, TreeSpec]:
    """Flattens a pytree into a list of values and a TreeSpec that can be used
    to reconstruct the pytree.
    """
    if _is_leaf(pytree):
        return [pytree], LeafSpec()

    flatten_fn = SUPPORTED_NODES[type(pytree)].flatten_fn
    child_pytrees, context = flatten_fn(pytree)

    # Recursively flatten the children
    result = []
    children_specs = []
    for child in child_pytrees:
        flat, child_spec = tree_flatten(child)
        result += flat
        children_specs.append(child_spec)

    return result, TreeSpec(type(pytree), context, children_specs)


def tree_unflatten(values: List, spec: TreeSpec) -> 'pytree':
    """Given a list of values and a TreeSpec, builds a pytree.
    This is the inverse operation of `tree_flatten`.
    """
    if not isinstance(spec, TreeSpec):
        raise ValueError(
            f'tree_unflatten(values, spec): Expected `spec` to be instance of '
            f'TreeSpec but got item of type {type(spec)}.')
    if len(values) != spec.num_leaves:
        raise ValueError(
            f'tree_unflatten(values, spec): `values` has length {len(values)} '
            f'but the spec refers to a pytree that holds {spec.num_leaves} '
            f'items ({spec}).')
    if isinstance(spec, LeafSpec):
        return values[0]

    unflatten_fn = SUPPORTED_NODES[spec.type].unflatten_fn

    # Recursively unflatten the children
    start = 0
    end = 0
    child_pytrees = []
    for child_spec in spec.children_specs:
        end += child_spec.num_leaves
        child_pytrees.append(tree_unflatten(values[start:end], child_spec))
        start = end

    return unflatten_fn(child_pytrees, spec.context)